"""URL detection, one-auto-open-per-run, retry logic, media pages, WebKit degrade."""
import sys
import types

import pytest

import preview


@pytest.mark.parametrize("text, expected", [
    ("  ➜  Local:   http://localhost:5173/\n", ["http://localhost:5173/"]),
    ("\x1b[32m➜\x1b[39m  \x1b[1mLocal\x1b[22m: \x1b[36mhttp://localhost:\x1b[1m5173\x1b[22m/\x1b[39m\n",
     ["http://localhost:5173/"]),
    ("Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...\n", ["http://localhost:8000/"]),
    ("listening at http://127.0.0.1:3000.\n", ["http://127.0.0.1:3000"]),
    ("open (http://localhost:8080/app), then\n", ["http://localhost:8080/app"]),
    ("see http://localhost:4000/docs/(v2)\n", ["http://localhost:4000/docs/(v2)"]),
    ('url="http://[::1]:9000/x?y=1";\n', ["http://[::1]:9000/x?y=1"]),
    ("bound http://[::]:9001\n", ["http://localhost:9001"]),
    ("HTTPS://LOCALHOST:8443/!\n", ["https://localhost:8443/"]),
    ("serving on port 6390\n", ["http://localhost:6390"]),
    ("12:01:22 PM [express] serving on port 5000\n", ["http://localhost:5000"]),
    ("Server listening on port: 3001\n", ["http://localhost:3001"]),
    ("http://example.com:8080/ and http://192.168.1.4:3000\n", []),
    ("http://localhost/ has no port\n", []),
    ("http://localhost:99999/\n", []),
    ("file:///tmp/x.html http://localhost.evil.com:80/\n", []),
    ("a http://localhost:1/ b http://localhost:1/ c http://localhost:2\n",
     ["http://localhost:1/", "http://localhost:2"]),
])
def test_find_urls(text, expected):
    assert preview.find_urls(text) == expected


@pytest.mark.parametrize("url, ok", [
    ("http://localhost:3000/", True), ("https://127.0.0.1:1/", True), ("http://[::1]:8/", True),
    ("http://127.4.5.6:80", True), ("http://example.com:3000", False), ("file:///etc/passwd", False),
    ("javascript:alert(1)", False), ("http://localhost.evil.com:80/", False),
])
def test_is_loopback_url(url, ok):
    assert preview.is_loopback_url(url) is ok


def test_watcher_auto_opens_once_per_run_and_joins_split_lines():
    watcher = preview.UrlWatcher()
    assert watcher.feed("Local: http://local", "stdout") == []
    assert watcher.feed("host:5173/\nNetwork: http://127.0.0.1:5173/\n", "stdout") == [
        ("http://localhost:5173/", True), ("http://127.0.0.1:5173/", False)]
    assert watcher.feed("again http://localhost:5173/\n", "stderr") == []
    assert watcher.feed("api http://localhost:8080\n", "stderr") == [("http://localhost:8080", False)]
    watcher.reset()  # a new run may auto-open again
    assert watcher.feed("http://localhost:5173/\n") == [("http://localhost:5173/", True)]


def test_watcher_keeps_streams_apart_and_flushes():
    watcher = preview.UrlWatcher()
    assert watcher.feed("http://localhost:", "stdout") == []
    assert watcher.feed("noise\n", "stderr") == []
    assert watcher.feed("7000", "stdout") == []
    assert watcher.flush() == [("http://localhost:7000", True)]


class FakeClock:
    def __init__(self):
        self.pending = {}
        self.next = 1

    def schedule(self, ms, fn):
        handle, self.next = self.next, self.next + 1
        self.pending[handle] = (ms, fn)
        return handle

    def cancel(self, handle):
        del self.pending[handle]

    def fire(self):
        handle = min(self.pending)
        _, fn = self.pending.pop(handle)
        fn()


def make_retry(**kwargs):
    clock, loads, gave_up = FakeClock(), [], []
    retry = preview.LoadRetry(loads.append, clock.schedule, clock.cancel, gave_up.append, **kwargs)
    return retry, clock, loads, gave_up


def test_retry_until_server_answers():
    retry, clock, loads, _ = make_retry()
    retry.start("http://localhost:6390")
    assert loads == ["http://localhost:6390"]
    for _ in range(3):
        assert retry.failed("http://localhost:6390/")
        assert retry.failed("http://localhost:6390/")  # duplicate failure: still one timer
        assert len(clock.pending) == 1
        clock.fire()
    assert len(loads) == 4
    retry.succeeded("http://localhost:6390/")
    assert not retry.active and not clock.pending


def test_retry_gives_up_after_limit():
    retry, clock, loads, gave_up = make_retry(interval_ms=1000, limit_ms=3000)
    retry.start("http://localhost:1")
    while retry.failed("http://localhost:1"):
        clock.fire()
    assert len(loads) == 4 and gave_up == ["http://localhost:1"] and not retry.active


@pytest.mark.parametrize("how", ["cancel", "restart"])
def test_retry_cancelled_by_stop_new_run_or_navigation(how):
    retry, clock, loads, _ = make_retry()
    retry.start("http://localhost:1")
    retry.failed("http://localhost:1")
    if how == "cancel":
        retry.cancel()
        assert not clock.pending and not retry.active
        assert retry.failed("http://localhost:1") is False
    else:
        retry.start("http://localhost:2")
        assert not clock.pending
        assert retry.failed("http://localhost:1") is False  # stale failure ignored


def test_media_kinds_and_pages(tmp_path):
    clip = tmp_path / "my clip & co.mp4"
    clip.write_bytes(b"")
    page, base = preview.media_page(clip)
    assert base == tmp_path.as_uri() + "/"
    assert 'src="my%20clip%20%26%20co.mp4"' in page and "my clip &amp; co.mp4" in page
    assert "gst-libav" in page and 'addEventListener("error"' in page
    image_page, _ = preview.media_page(tmp_path / "a.PNG")
    assert "<img" in image_page
    assert preview.media_page(tmp_path / "index.html") is None
    assert preview.media_kind(tmp_path / "index.html") == "document"
    assert preview.media_kind(tmp_path / "x.svg") == "document"
    assert preview.media_kind(tmp_path / "x.py") is None


def test_strip_ansi_osc_links():
    text = "\x1b]8;;http://localhost:3000\x07click\x1b]8;;\x07 http://localhost:3000/\n"
    assert preview.strip_ansi(text).startswith("click ")
    assert preview.find_urls(text) == ["http://localhost:3000/"]


def test_webkit_missing_degrades(monkeypatch):
    import gi
    import preview_pane
    monkeypatch.setattr(preview_pane, "_WEBKIT", [])
    real = gi.require_version

    def refuse(namespace, version):
        if namespace == "WebKit2":
            raise ValueError("Namespace WebKit2 not available")
        return real(namespace, version)

    monkeypatch.setattr(gi, "require_version", refuse)
    assert preview_pane.load_webkit() is None
    assert preview_pane.load_webkit() is None  # cached, not retried


def test_webkit_falls_back_to_4_0(monkeypatch):
    import gi
    import preview_pane
    monkeypatch.setattr(preview_pane, "_WEBKIT", [])
    asked = []

    def fake(namespace, version):
        asked.append(version)
        if version == "4.1":
            raise ValueError("Namespace WebKit2 not available for version 4.1")

    fake_repo = types.ModuleType("gi.repository")
    fake_repo.WebKit2 = object()
    monkeypatch.setattr(gi, "require_version", fake)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    assert preview_pane.load_webkit() is fake_repo.WebKit2
    assert asked == ["4.1", "4.0"]
