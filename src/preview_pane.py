"""The Preview dock tab: a small WebKit2 browser for local dev servers and files.

WebKit2 is imported lazily (the first time the tab is shown or a page is
opened), so it costs nothing at startup and its absence never stops the app.
Without WebKit2 the tab explains what to install and can still hand URLs to
the system browser.
"""
import urllib.parse

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

import preview

_WEBKIT = []  # [module or None] once tried


def load_webkit():
    """WebKit2 module (4.1, else 4.0) or None. Tried once per process."""
    if not _WEBKIT:
        module = None
        import gi
        for version in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", version)
                from gi.repository import WebKit2
                module = WebKit2
                break
            except (ImportError, ValueError):
                continue
        _WEBKIT.append(module)
    return _WEBKIT[0]


INSTALL_HINT = ("Preview needs WebKitGTK.\n\n"
                "Garuda / Arch:  sudo pacman -S --needed webkit2gtk-4.1\n"
                "Ubuntu / Debian:  sudo apt install gir1.2-webkit2-4.1\n\n"
                "Then restart ForgePane. Everything else works without it, and\n"
                "↗ still opens addresses in your normal browser.")


class PreviewPane:
    def __init__(self, show_tab, status, auto_open=True):
        self.show_tab = show_tab       # callable: switch the dock to this tab
        self.status = status           # Gtk.Label for one-line notices
        self.view = None
        self.webkit = None
        self.tried = False
        self.file_ok = False
        self.load_failed = False
        self.watcher = preview.UrlWatcher()
        self.retry = preview.LoadRetry(self._load_uri, GLib.timeout_add, GLib.source_remove,
                                       self._gave_up)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.set_border_width(4)
        bar = Gtk.Box(spacing=4)
        self.back = Gtk.Button(label="◀", tooltip_text="Back", sensitive=False)
        self.reload = Gtk.Button(label="⟳", tooltip_text="Reload")
        self.url = Gtk.Entry(placeholder_text="http://localhost:3000  (Ctrl+L)")
        self.external = Gtk.Button(label="↗", tooltip_text="Open in your web browser")
        self.auto = Gtk.CheckButton(label="Auto-open", active=auto_open,
                                    tooltip_text="Open localhost addresses a run prints, once per run")
        self.back.connect("clicked", lambda _: self.go_back())
        self.reload.connect("clicked", lambda _: self.do_reload())
        self.url.connect("activate", lambda _: self.navigate(self.url.get_text()))
        self.external.connect("clicked", lambda _: self.open_external())
        for widget in (self.back, self.reload):
            bar.pack_start(widget, False, False, 0)
        bar.pack_start(self.url, True, True, 0)
        bar.pack_start(self.external, False, False, 0)
        bar.pack_start(self.auto, False, False, 0)
        self.box.pack_start(bar, False, False, 0)
        self.area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.placeholder = Gtk.Label(label="Run a dev server, or pick a file with Open in Preview.",
                                     wrap=True, justify=Gtk.Justification.CENTER)
        self.placeholder.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.area.pack_start(self.placeholder, True, True, 0)
        self.box.pack_start(self.area, True, True, 0)
        self.box.connect("key-press-event", self._key)

    # ---- WebKit setup -----------------------------------------------------

    def ensure(self):
        """Create the web view on first use. Returns False if WebKit2 is missing."""
        if self.view is not None:
            return True
        if self.tried:
            return False
        self.tried = True
        self.webkit = load_webkit()
        if self.webkit is None:
            self.placeholder.set_text(INSTALL_HINT)
            self.placeholder.set_selectable(True)
            for widget in (self.back, self.reload, self.auto):
                widget.set_sensitive(False)
            return False
        WebKit2 = self.webkit
        self.view = WebKit2.WebView()
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_allow_file_access_from_file_urls(False)
        settings.set_allow_universal_access_from_file_urls(False)
        settings.set_javascript_can_open_windows_automatically(False)
        self.view.connect("load-changed", self._load_changed)
        self.view.connect("load-failed", self._load_failed)
        self.view.connect("decide-policy", self._decide_policy)
        self.view.connect("notify::uri", lambda view, _: self._show_uri(view.get_uri()))
        self.area.remove(self.placeholder)
        self.area.pack_start(self.view, True, True, 0)
        self.view.show()
        return True

    def _load_uri(self, uri):
        if self.ensure():
            self.view.load_uri(uri)

    def _show_uri(self, uri):
        if uri and not uri.startswith("about:") and not self.url.has_focus():
            self.url.set_text(uri)

    def _load_changed(self, view, event):
        WebKit2 = self.webkit
        if event == WebKit2.LoadEvent.STARTED:
            self.load_failed = False
        elif event == WebKit2.LoadEvent.COMMITTED:
            self.file_ok = False
        elif event == WebKit2.LoadEvent.FINISHED and not self.load_failed:
            self.retry.succeeded(view.get_uri() or "")
        self.back.set_sensitive(view.can_go_back())

    def _load_failed(self, view, _event, uri, error):
        self.load_failed = True
        if error.matches(self.webkit.NetworkError.quark(), int(self.webkit.NetworkError.CANCELLED)):
            return True
        if self.retry.failed(uri):
            self.status.set_text(f"Preview: waiting for {uri} …")
            return True  # no error page while the server is still starting
        return False

    def _gave_up(self, url):
        self.status.set_text(f"Preview: {url} didn't answer within 30 s.")
        if self.view is not None:
            self.view.load_html(
                "<body style='background:#191d25;color:#e8edf7;font:14px sans-serif;padding:2em'>"
                f"<p>Nothing answered at <b>{GLib.markup_escape_text(url)}</b> within 30 seconds.</p>"
                "<p>Check the Output tab, then press ⟳ to try again.</p></body>", None)

    def _decide_policy(self, view, decision, kind):
        WebKit2 = self.webkit
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                        WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri() or ""
        scheme = urllib.parse.urlsplit(uri).scheme.lower()
        if scheme == "file" and not self.file_ok:
            decision.ignore()
            return True
        if scheme not in {"http", "https", "file", "about", "data", "blob"}:
            decision.ignore()
            return True
        if action.is_user_gesture():
            self.retry.cancel()
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()  # keep target=_blank links in this one view
            if scheme in {"http", "https"}:
                view.load_uri(uri)
            return True
        return False

    def _key(self, _widget, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.url.grab_focus()
            self.url.select_region(0, -1)
            return True
        return False

    # ---- Actions ----------------------------------------------------------

    def navigate(self, text):
        """The user typed an address."""
        text = text.strip()
        if not text:
            return
        if "://" not in text and not text.startswith(("about:", "data:")):
            text = "http://" + text
        self.retry.cancel()
        if text.lower().startswith("file:"):
            self.file_ok = True
        if self.ensure():
            self.view.load_uri(text)
            self.view.grab_focus()
        else:
            self.url.set_text(text)

    def go_back(self):
        self.retry.cancel()
        if self.view is not None and self.view.can_go_back():
            self.view.go_back()

    def do_reload(self):
        self.retry.cancel()
        if self.view is not None:
            if self.view.get_uri():
                self.view.reload()
            elif self.url.get_text():
                self.navigate(self.url.get_text())

    def open_external(self):
        uri = (self.view.get_uri() if self.view is not None else None) or self.url.get_text().strip()
        if not uri or uri.startswith("about:"):
            return
        if "://" not in uri:
            uri = "http://" + uri
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            self.status.set_text(f"Couldn't open a browser: {exc.message}")

    def open_path(self, path):
        """Show a local file the user picked (image, video, HTML, SVG, PDF)."""
        kind = preview.media_kind(path)
        if kind is None:
            raise ValueError(f"Preview shows web pages, images, and video, not {path.name}.")
        self.show_tab()
        self.retry.cancel()
        if not self.ensure():
            self.url.set_text(path.as_uri())
            return
        self.file_ok = True
        page = preview.media_page(path)
        if page:
            self.view.load_html(*page)
        else:
            self.view.load_uri(path.as_uri())
        self.url.set_text(path.as_uri())

    # ---- Hooks from the Run/Output side ----------------------------------

    def run_started(self):
        self.retry.cancel()
        self.watcher.reset()

    def run_stopped(self):
        self.retry.cancel()

    def feed(self, text, stream):
        for url, auto in self.watcher.feed(text, stream):
            if not preview.is_loopback_url(url):
                continue
            if auto and self.auto.get_active():
                self.show_tab()
                if self.ensure():
                    self.retry.start(url)
                    self.status.set_text(f"Preview: opening {url}")
                else:
                    self.url.set_text(url)
                    self.status.set_text(f"Found {url}. Install WebKitGTK to preview it here, or press ↗.")
            else:
                if not self.url.get_text() or auto:
                    self.url.set_text(url)
                self.status.set_text(f"Found {url} in the output. Open the Preview tab to view it.")
