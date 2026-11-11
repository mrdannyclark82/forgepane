"""FUD (Files Up Display) as a ForgePane plugin.

Danny's ruling (2026-11-10): FUD should live inside ForgePane as a
plugin, not as a separate hand-started dashboard.

What it does: adds "FUD — Files Up Display" to the Plugins menu. The
window shows, natively:

- the latest scan's headline numbers (files, folders, scan errors,
  projects found, items waiting in the cleanup queues);
- the six cleanup queues (Q1-Q6) with their entries — title, tier, and
  summary — straight from the newest slim report;
- the live file tree: the current workspace folder browsed through
  FUD's own /tree/children API, exactly like FUD's web tree.

Honest limits, on purpose:
- The data comes from FUD's latest SAVED scan report (the slim JSON in
  the FUD history folder). If no report exists, the window says so.
- The live tree needs the FUD server running; if it is down, the window
  says so plainly. Scanning still happens in FUD itself (POST /scan is
  token-gated) — this plugin displays; FUD's read-only law holds: it
  never deletes or moves anything.
"""

import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

FUD_HOME = Path.home() / "files-up-display"
FUD_BASE = "http://127.0.0.1:8767"

# FUD's slim report holds counts + project structure + queues only —
# no per-file inventory — so the summary renders from those fields.
_SUMMARY_FIELDS = (("files", "Files"), ("folders", "Folders"),
                   ("errors", "Scan errors"))


def _get(path, timeout=10):
    """GET a FUD loopback endpoint. Same client discipline as core.py:
    no proxy, no redirects — local content stays local."""
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = Request(FUD_BASE + path)
    with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=timeout) as response:
        return response.read()


def latest_report():
    history = FUD_HOME / "history"
    reports = sorted(history.glob("report-*-slim.json"))
    if not reports:
        return None, None
    newest = reports[-1]
    return json.loads(newest.read_text()), newest.name


def _label(text, css_class=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    if css_class:
        widget.get_style_context().add_class(css_class)
    return widget


def _scrolled(widget):
    pane = Gtk.ScrolledWindow()
    pane.add(widget)
    return pane


def _text_view():
    view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                        wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        top_margin=8, left_margin=8, right_margin=8)
    return view


class FudWindow:
    def __init__(self, workspace):
        self.workspace = workspace
        self.window = Gtk.Window(title="FUD — Files Up Display",
                                 transient_for=workspace.window,
                                 destroy_with_parent=True, resizable=True)
        self.window.set_default_size(760, 560)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin=10)
        self.window.add(layout)

        self.report, self.report_name = latest_report()
        layout.pack_start(self._summary_box(), False, False, 0)

        notebook = Gtk.Notebook()
        layout.pack_start(notebook, True, True, 0)
        notebook.append_page(self._queues_page(), _label("Cleanup queues"))
        notebook.append_page(self._tree_page(), _label("Live tree (FUD)"))

        self.window.show_all()

    # -- summary ------------------------------------------------------
    def _summary_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if self.report is None:
            box.pack_start(_label(
                "No FUD scan report found yet. Run a scan in FUD first — "
                "this panel reads FUD's latest saved report."), False, False, 0)
            return box
        report = self.report
        box.pack_start(_label("Latest scan", "section"), False, False, 0)
        counts = report.get("counts") or {}
        bits = [f"{caption}: {counts[key]}" for key, caption in _SUMMARY_FIELDS
                if key in counts]
        bits.append(f"Projects: {len(report.get('projects') or [])}")
        bits.append(f"Waiting in queues: {report.get('queue_total', 0)}")
        bits.append(f"Connections mapped: {len(report.get('connections') or [])}")
        box.pack_start(_label("   ·   ".join(bits)), False, False, 0)
        box.pack_start(_label(
            f"Scanned {report.get('scanned_at', '?')} · root "
            f"{report.get('root', '?')} · report {self.report_name}"),
            False, False, 0)
        return box

    # -- cleanup queues ----------------------------------------------
    def _queues_page(self):
        view = _text_view()
        buffer = view.get_buffer()
        if self.report is None:
            buffer.set_text("No report loaded.\n")
            return _scrolled(view)
        lines = []
        for queue in self.report.get("queues") or []:
            entries = queue.get("entries") or []
            overflow = queue.get("overflow") or 0
            lines.append(f"{queue.get('label', queue.get('key', '?'))} — "
                         f"{len(entries)} shown"
                         + (f", {overflow} more" if overflow else ""))
            for entry in entries:
                lines.append(f"  • {entry.get('title', '?')}"
                             f"  [{entry.get('tier', '')}]")
                summary = (entry.get("summary") or "").strip()
                if summary:
                    lines.append(f"    {summary[:280]}")
                for evidence in (entry.get("evidence") or [])[:3]:
                    lines.append(f"    - {str(evidence)[:200]}")
            lines.append("")
        buffer.set_text("\n".join(lines) or "The report lists no queues.\n")
        return _scrolled(view)

    # -- live tree ------------------------------------------------------
    def _tree_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tree_status = _label("Asking FUD for the tree…")
        box.pack_start(self.tree_status, False, False, 0)
        store = Gtk.TreeStore(str, str)  # visible name, full path
        view = Gtk.TreeView(model=store)
        view.append_column(Gtk.TreeViewColumn("Name", Gtk.CellRendererText(),
                                              text=0))
        view.connect("row-expanded", self._tree_expanded)
        box.pack_start(_scrolled(view), True, True, 0)
        self.tree_store = store
        root = str(self.workspace.root)
        parent = store.append(None, [Path(root).name or root, root])
        store.append(parent, ["Loading…", ""])
        self.tree_status.set_text(
            f"Browsing {root} through FUD's tree — expand a folder to load it.")
        return box

    def _tree_expanded(self, _view, tree_iter, _path):
        store = self.tree_store
        path = store[tree_iter][1]
        if not path:
            return
        child = store.iter_children(tree_iter)
        loading_only = (child is not None
                        and store.iter_n_children(tree_iter) == 1
                        and not store[child][1])
        if not loading_only:
            return  # already loaded (or genuinely empty)

        def work():
            raw = _get("/tree/children?path=" + quote(path, safe=""),
                       timeout=20)
            return json.loads(raw)

        def success(payload):
            while store.iter_n_children(tree_iter) > 0:
                store.remove(store.iter_children(tree_iter))
            entries = payload.get("children") or payload.get("entries") or []
            shown = 0
            for entry in entries:
                if isinstance(entry, dict):
                    name = entry.get("name") or ""
                    full = entry.get("path") or str(Path(path) / name)
                    is_dir = bool(entry.get("is_dir", entry.get("dir")))
                else:
                    name, full, is_dir = str(entry), str(entry), False
                if not name:
                    continue
                node = store.append(tree_iter,
                                    [("▸ " if is_dir else "· ") + name, full])
                if is_dir:
                    store.append(node, ["Loading…", ""])
                shown += 1
            if not shown:
                store.append(tree_iter, ["(empty)", ""])
            self.tree_status.set_text(f"{path} — {shown} entries via FUD.")

        def failure(message):
            while store.iter_n_children(tree_iter) > 0:
                store.remove(store.iter_children(tree_iter))
            store.append(tree_iter, ["(FUD unreachable)", ""])
            self.tree_status.set_text(
                "FUD isn't answering on 127.0.0.1:8767 — start FUD and "
                "reopen this window. The summary and queues above come "
                "from the saved report and work either way.")

        self.workspace.background("fud-tree", work, success, failure)


def show_fud(workspace):
    window = getattr(workspace, "_fud_window", None)
    if window is not None:
        window.window.present()
        return
    workspace._fud_window = FudWindow(workspace)


def register(workspace):
    workspace.add_plugin_action("FUD — Files Up Display", show_fud)
