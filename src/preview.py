"""Preview tab logic — no widgets here, so it is testable without a display.

* ``find_urls(text)`` pulls loopback dev-server URLs out of program output:
  http(s)://localhost, 127.x.x.x, 0.0.0.0, [::1] or [::] with an explicit
  port. ANSI colour codes and trailing punctuation are stripped, and
  0.0.0.0 / [::] become localhost (they are bind addresses, not places a
  browser can go). A plain "serving on port 6390" line, which servers such
  as Express print instead of a URL, maps to http://localhost:6390.
* ``UrlWatcher`` feeds on streamed output (chunks may split lines) and
  reports each new URL once, flagging only the first one per run for
  auto-open.
* ``LoadRetry`` re-tries a page load while a dev server is still coming up:
  every second for up to 30 s, cancelled by Stop, a new run, or the user
  navigating somewhere else.
* ``media_kind`` / ``media_page`` decide how Preview shows a local file the
  user picked: images and video get a small HTML wrapper (with a codec hint
  if the video can't play), HTML and SVG load directly.

Only URLs found in output are ever auto-loaded, and those are always
loopback http(s). file:// is loaded only when the user picks a file.
"""
import html
import re
import urllib.parse
from pathlib import Path

RETRY_INTERVAL_MS = 1000
RETRY_LIMIT_MS = 30000

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_URL = re.compile(
    r"(?P<scheme>https?)://(?P<host>localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\])"
    r":(?P<port>\d{1,5})(?P<rest>[/?#][^\s<>\"'`]*)?", re.IGNORECASE)
_PORT_LINE = re.compile(
    r"\b(?:listening|serving|running|started|available|ready)\b[^\n]{0,40}?\bport\s*:?\s*(\d{2,5})\b",
    re.IGNORECASE)
_TRAILING = ".,;:!?'\"*>]}"

_INSTALL_CODECS = ("Garuda/Arch: sudo pacman -S --needed gst-libav gst-plugins-good · "
                   "Debian/Ubuntu: sudo apt install gstreamer1.0-libav gstreamer1.0-plugins-good")

VIDEO = {".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ogv"}
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif"}
DOCUMENT = {".html", ".htm", ".svg", ".pdf"}


def strip_ansi(text):
    return _ANSI.sub("", text)


def _clean_rest(rest):
    rest = rest or ""
    while rest and rest[-1] in _TRAILING:
        rest = rest[:-1]
    while rest.endswith(")") and rest.count(")") > rest.count("("):
        rest = rest[:-1]
    return rest


def find_urls(text):
    """Loopback URLs in text, normalized, in order, without duplicates."""
    found = []
    for line in strip_ansi(text).splitlines():
        matched = False
        for match in _URL.finditer(line):
            port = int(match["port"])
            if not 0 < port < 65536:
                continue
            host = match["host"].lower()
            if host in {"0.0.0.0", "[::]"}:
                host = "localhost"
            url = f"{match['scheme'].lower()}://{host}:{port}{_clean_rest(match['rest'])}"
            matched = True
            if url not in found:
                found.append(url)
        if not matched:
            port_match = _PORT_LINE.search(line)
            if port_match and 0 < int(port_match[1]) < 65536:
                url = f"http://localhost:{int(port_match[1])}"
                if url not in found:
                    found.append(url)
    return found


def is_loopback_url(url):
    """True only for http(s) URLs whose host is a loopback name."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme in {"http", "https"} and (
        host in {"localhost", "::1"} or re.fullmatch(r"127(?:\.\d{1,3}){3}", host) is not None)


class UrlWatcher:
    """Watches one run's output. feed() returns [(url, auto_open)] for new URLs."""

    MAX_PARTIAL = 8192

    def __init__(self):
        self.reset()

    def reset(self):
        self.seen = []
        self.opened = False
        self._partial = {}

    def feed(self, text, stream="stdout"):
        data = self._partial.get(stream, "") + text
        lines, _, tail = data.rpartition("\n")
        self._partial[stream] = tail[-self.MAX_PARTIAL:]
        return self._take(lines)

    def flush(self):
        pending = "\n".join(self._partial.values())
        self._partial = {}
        return self._take(pending)

    def _take(self, text):
        results = []
        for url in find_urls(text):
            if url in self.seen:
                continue
            self.seen.append(url)
            auto = not self.opened
            self.opened = True
            results.append((url, auto))
        return results


class LoadRetry:
    """Retry loading one URL while its server starts.

    schedule(ms, fn) -> handle and cancel(handle) are injectable for tests;
    the app passes GLib.timeout_add and GLib.source_remove.
    """

    def __init__(self, load, schedule, cancel, give_up=None,
                 interval_ms=RETRY_INTERVAL_MS, limit_ms=RETRY_LIMIT_MS):
        self.load, self.schedule, self.cancel_source = load, schedule, cancel
        self.give_up = give_up
        self.interval_ms, self.limit_ms = interval_ms, limit_ms
        self.url = None
        self.waited = 0
        self._handle = None

    @property
    def active(self):
        return self.url is not None

    def start(self, url):
        self.cancel()
        self.url = url
        self.waited = 0
        self.load(url)

    def failed(self, url):
        """Call when a load failed. Returns True if a retry was scheduled."""
        if not self.active or url.rstrip("/") != self.url.rstrip("/"):
            return False
        if self.waited >= self.limit_ms:
            gave_up = self.url
            self.cancel()
            if self.give_up:
                self.give_up(gave_up)
            return False
        if self._handle is None:
            self._handle = self.schedule(self.interval_ms, self._fire)
        return True

    def _fire(self):
        self._handle = None
        if self.active:
            self.waited += self.interval_ms
            self.load(self.url)
        return False

    def succeeded(self, url):
        if self.active and url.rstrip("/") == self.url.rstrip("/"):
            self.cancel()

    def cancel(self):
        if self._handle is not None:
            self.cancel_source(self._handle)
            self._handle = None
        self.url = None


def media_kind(path):
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO:
        return "video"
    if suffix in IMAGE:
        return "image"
    if suffix in DOCUMENT:
        return "document"
    return None


def media_page(path):
    """(html, base_uri) wrapping a local image or video; None for documents."""
    path = Path(path).resolve()
    kind = media_kind(path)
    if kind not in {"video", "image"}:
        return None
    src = urllib.parse.quote(path.name)
    name = html.escape(path.name)
    base = path.parent.as_uri() + "/"
    style = ("html,body{margin:0;height:100%;background:#191d25;color:#e8edf7;font:14px sans-serif}"
             "body{display:flex;flex-direction:column;align-items:center;justify-content:center}"
             "video,img{max-width:100%;max-height:calc(100% - 3em)}"
             "#hint{display:none;max-width:40em;padding:1em;text-align:center;color:#ff9b9b}"
             "p{color:#b2c5df;margin:.5em}")
    if kind == "image":
        body = f'<img src="{src}" alt="{name}"><p>{name}</p>'
    else:
        hint = html.escape("This video can't play here. The file may be damaged, or the GStreamer "
                           "codec for it is missing. "
                           + _INSTALL_CODECS)
        body = (f'<video id="v" src="{src}" controls autoplay muted></video><p>{name}</p>'
                f'<div id="hint">{hint}</div>'
                '<script>const v=document.getElementById("v");'
                'function bad(){v.style.display="none";document.getElementById("hint").style.display="block";}'
                'v.addEventListener("error",bad);'
                'if(v.error)bad();</script>')
    page = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{name}</title>'
            f'<style>{style}</style></head><body>{body}</body></html>')
    return page, base
