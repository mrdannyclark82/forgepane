#!/usr/bin/env python3
"""ForgePane desktop workspace."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path

import core

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkSource", "4")
    gi.require_version("Vte", "2.91")
except (ImportError, ValueError) as exc:
    raise SystemExit(f"ForgePane needs PyGObject, GTK 3, GtkSourceView 4, and VTE 2.91. "
                     f"See README.md for setup. ({exc})") from exc
from gi.repository import Gio, GLib, Gtk, GtkSource, Pango, Vte


def scrolled(widget):
    pane = Gtk.ScrolledWindow()
    pane.add(widget)
    return pane


def label(text, css_class=None):
    widget = Gtk.Label(label=text, xalign=0)
    if css_class:
        widget.get_style_context().add_class(css_class)
    return widget


class Workspace:
    def __init__(self, app, root):
        self.root = root
        self.folder = root
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.busy = set()
        self.documents = {}
        self.chat_history = []
        self.review_port = None
        self.settings = {"model": "", "editor_scheme": "oblivion", "font": "Monospace 11",
                         "chat_width": 550, "chat_height": 410, "shortcuts": {},
                         "enabled_plugins": []}
        try:
            self.settings.update(json.loads((core.CONFIG / "settings.json").read_text()))
        except FileNotFoundError:
            pass
        self.app = app
        self.window = Gtk.ApplicationWindow(application=app, title=f"ForgePane · {root.name}")
        self.window.set_default_size(1260, 780)
        self.window.connect("delete-event", self.on_close)
        self.actions = Gtk.AccelGroup()
        self.window.add_accel_group(self.actions)
        header = Gtk.HeaderBar(title="ForgePane", subtitle=str(root), show_close_button=True)
        self.window.set_titlebar(header)
        self.menu = Gtk.Menu()
        self.plugins = Gtk.Menu()
        self.section(self.menu, "Plugins", self.plugins)
        self.status = label("Ready", "status")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.add(frame)
        main = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=250)
        frame.pack_start(main, True, True, 0)
        frame.pack_end(self.status, False, False, 0)

        files = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        files.set_size_request(160, -1)
        files.set_border_width(10)
        self.location = label("WORKSPACE", "section")
        self.location.set_ellipsize(Pango.EllipsizeMode.START)
        files.pack_start(self.location, False, False, 0)
        self.file_filter = Gtk.SearchEntry(placeholder_text="Filter current folder")
        self.file_filter.connect("search-changed", lambda _: self.refresh_files())
        files.pack_start(self.file_filter, False, False, 0)
        self.file_store = Gtk.ListStore(str, str, str)
        self.file_list = Gtk.TreeView(model=self.file_store, headers_visible=False, enable_search=False)
        for column in range(2):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            self.file_list.append_column(Gtk.TreeViewColumn("", renderer, text=column))
        self.file_list.connect("row-activated", self.activate_path)
        files.pack_start(scrolled(self.file_list), True, True, 0)
        self.button(files, "Parent folder", lambda: self.go(self.folder.parent))
        main.pack1(files, resize=False, shrink=False)

        right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=640)
        main.pack2(right, resize=True, shrink=False)
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.pack1(center, resize=True, shrink=False)
        self.tabs = Gtk.Notebook(scrollable=True)
        self.tabs.connect("switch-page", lambda *_: GLib.idle_add(self.update_status))
        center.pack_start(self.tabs, True, True, 0)
        self.welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                               valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.welcome.pack_start(label("ForgePane", "welcome"), False, False, 0)
        self.welcome.pack_start(label("Open a file from the workspace to start editing."), False, False, 0)
        self.button(self.welcome, "Open file", self.choose_file)
        self.tabs.append_page(self.welcome, label("Workspace"))

        self.dock = Gtk.Notebook()
        self.dock.set_size_request(250, -1)
        right.pack2(self.dock, resize=False, shrink=False)
        terminal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(3000)
        self.terminal.connect("child-exited", self.shell_exited)
        self.shell_active = False
        terminal_box.pack_start(self.terminal, True, True, 0)
        self.button(terminal_box, "Start shell", self.start_shell)
        self.dock.append_page(terminal_box, label("Terminal"))

        output, self.output = self.transcript()
        self.dock.append_page(output, label("Output"))
        review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        review.set_border_width(8)
        review_view, self.review_text = self.transcript()
        self.review_note = Gtk.Entry(placeholder_text="Comment on the cursor line")
        review.pack_start(review_view, True, True, 0)
        review.pack_start(self.review_note, False, False, 0)
        self.review_note.connect("activate", lambda _: self.review_request(True))
        self.button(review, "Post line comment", lambda: self.review_request(True))
        self.button(review, "Connect review session", self.connect_review)
        self.dock.append_page(review, label("Review"))

        for title, callback in (("Open", self.choose_file), ("Save", self.save),
                                ("Terminal", self.toggle_terminal), ("AI Chat", self.show_chat)):
            control = Gtk.Button(label=title)
            control.connect("clicked", lambda _, fn=callback: self.guard(fn))
            (header.pack_start if title in {"Open", "Save"} else header.pack_end)(control)
        menu_button = Gtk.MenuButton(label="Tools")
        menu_button.set_popup(self.menu)
        header.pack_end(menu_button)

        file_menu, git_menu, integrations = Gtk.Menu(), Gtk.Menu(), Gtk.Menu()
        self.section(self.menu, "Files", file_menu, 0)
        self.section(self.menu, "Git", git_menu, 1)
        self.section(self.menu, "Integrations", integrations, 2)
        self.command(file_menu, "Open File", self.choose_file, "<Primary>o")
        self.command(file_menu, "New File", self.new_file, "<Primary>n")
        self.command(file_menu, "New Folder", self.new_folder)
        self.command(file_menu, "Reload from Disk", self.reload_document)
        self.command(file_menu, "Open Folder", self.open_folder, "<Primary><Shift>o")
        self.command(file_menu, "Rename Selected", self.rename_selected, "F2")
        self.command(file_menu, "Save", self.save, "<Primary>s")
        self.command(file_menu, "Close Tab", self.close_tab, "<Primary>w")
        self.command(file_menu, "Focus File Filter", self.file_filter.grab_focus, "<Primary>p")
        self.command(file_menu, "Find in Editor", self.find, "<Primary>f")
        self.command(file_menu, "Next Tab", self.tabs.next_page, "<Primary>Page_Down")
        self.command(file_menu, "Previous Tab", self.tabs.prev_page, "<Primary>Page_Up")
        self.command(file_menu, "Refresh Files", self.refresh_files, "F5")
        for action in ("status", "diff", "fetch", "pull", "push"):
            self.command(git_menu, action.title(), lambda value=action: self.git(value))
        self.command(integrations, "Terminal Sidebar", self.toggle_terminal, "<Primary>grave")
        self.command(integrations, "AI Chat", self.show_chat, "<Primary><Shift>a")
        self.command(integrations, "Code Review", lambda: self.show_dock(2), "<Primary><Shift>r")
        self.command(integrations, "Cloud Sync", self.cloud)
        for ide in ("VS Code", "IntelliJ IDEA"):
            self.command(integrations, "Open in " + ide, lambda name=ide: self.open_in_ide(name))
        self.command(self.menu, "Edit Settings and Theme", self.open_settings)
        self.menu.show_all()
        self.apply_theme()
        self.refresh_files()
        self.load_plugins()
        self.window.show_all()
        GLib.timeout_add_seconds(3, self.poll_review)

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.status.set_text(str(exc))

    def button(self, container, title, callback):
        button = Gtk.Button(label=title)
        button.connect("clicked", lambda _: self.guard(callback))
        container.pack_start(button, False, False, 0)

    def section(self, menu, name, submenu, position=None):
        item = Gtk.MenuItem(label=name)
        item.set_submenu(submenu)
        if position is None:
            menu.append(item)
        else:
            menu.insert(item, position)

    def command(self, menu, name, fn, shortcut=""):
        item = Gtk.MenuItem(label=name)
        item.connect("activate", lambda _: self.guard(fn))
        menu.append(item)
        shortcut = self.settings["shortcuts"].get(name, shortcut)
        if shortcut:
            key, mods = Gtk.accelerator_parse(shortcut)
            if key:
                item.add_accelerator("activate", self.actions, key, mods, Gtk.AccelFlags.VISIBLE)
        item.show()

    def apply_theme(self):
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name("theme.css")))
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = core.CONFIG / "theme.css"
        if theme.is_file():
            custom = Gtk.CssProvider()
            custom.load_from_path(str(theme))
            Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), custom,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def ask(self, title, fields, choices=None):
        dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        for caption, response in choices or [("OK", Gtk.ResponseType.OK)]:
            dialog.add_button(caption, response)
        dialog.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=14)
        dialog.get_content_area().add(grid)
        entries = []
        for row, (caption, default) in enumerate(fields):
            entry = Gtk.Entry(text=default, width_chars=34, activates_default=True)
            grid.attach(label(caption), 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            entries.append(entry)
        dialog.show_all()
        result = dialog.run()
        values = [entry.get_text() for entry in entries]
        dialog.destroy()
        return result, values

    def confirm(self, question):
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text=question)
        result = dialog.run()
        dialog.destroy()
        return result == Gtk.ResponseType.OK

    def choose(self, mode):
        dialog = Gtk.FileChooserDialog(title="Choose path", transient_for=self.window, action=mode)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        dialog.set_current_folder(str(self.folder))
        result = Path(dialog.get_filename()) if dialog.run() == Gtk.ResponseType.OK else None
        dialog.destroy()
        return result

    def choose_file(self):
        path = self.choose(Gtk.FileChooserAction.OPEN)
        if path:
            self.open_file(path)

    def open_folder(self):
        path = self.choose(Gtk.FileChooserAction.SELECT_FOLDER)
        if path:
            self.root = path.resolve()
            self.window.get_titlebar().set_subtitle(str(self.root))
            self.go(self.root)

    def go(self, folder):
        self.folder = folder.resolve()
        self.file_filter.set_text("")
        self.refresh_files()

    def refresh_files(self):
        self.location.set_text(self.folder.name or "/")
        self.location.set_tooltip_text(str(self.folder))
        query = self.file_filter.get_text().casefold()
        children = sorted(((not path.is_dir(), path.name.casefold(), path)
                           for path in self.folder.iterdir() if query in path.name.casefold()))
        self.file_store.clear()
        for is_file, _, path in children:
            self.file_store.append(["·" if is_file else "▸", path.name, str(path)])

    def activate_path(self, _, index, _column):
        path = Path(self.file_store[index][2])
        self.guard(lambda: self.open_file(path) if not path.is_dir() else self.go(path))

    def new_file(self):
        response, (name,) = self.ask("New file", [("Filename", "")])
        if response == Gtk.ResponseType.OK:
            path = self.child_path(name)
            path.touch(exist_ok=False)
            self.refresh_files()
            self.open_file(path)

    def new_folder(self):
        response, (name,) = self.ask("New folder", [("Folder name", "")])
        if response == Gtk.ResponseType.OK:
            self.child_path(name).mkdir()
            self.refresh_files()

    def reload_document(self):
        doc = self.document()
        if not doc:
            return
        buf = doc["buffer"]
        if buf.get_modified() and not self.confirm("Discard unsaved edits and reload from disk?"):
            return
        text, revision = core.read_document(doc["path"])
        buf.begin_not_undoable_action()
        try:
            buf.set_text(text)
        finally:
            buf.end_not_undoable_action()
        buf.set_modified(False)
        doc["revision"] = revision
        self.status.set_text(f"Reloaded {doc['path'].name}")

    def child_path(self, name):
        if not name or name in {".", ".."} or Path(name).name != name:
            raise ValueError("Enter a single filename.")
        return self.folder / name

    def rename_selected(self):
        model, selected = self.file_list.get_selection().get_selected()
        if selected is None:
            return
        path = Path(model[selected][2])
        if any(doc["path"] == path.resolve() or path.resolve() in doc["path"].parents
               for doc in self.documents.values()):
            raise ValueError("Close affected editor tabs before renaming.")
        response, (name,) = self.ask("Rename", [("New name", path.name)])
        if response == Gtk.ResponseType.OK and name != path.name:
            target = self.child_path(name)
            if target.exists() or target.is_symlink():
                raise FileExistsError(target)
            path.rename(target)
            self.refresh_files()

    def open_file(self, path):
        path = path.resolve()
        for page, document in self.documents.items():
            if document["path"] == path:
                self.tabs.set_current_page(self.tabs.page_num(page))
                document["view"].grab_focus()
                return
        content, revision = core.read_document(path)
        buffer = GtkSource.Buffer()
        manager = GtkSource.LanguageManager.get_default()
        detected = core.detect_language(path, content)
        language = manager.get_language(detected) if detected else None
        if language is None:
            language = manager.guess_language(str(path), None)
        if language:
            buffer.set_language(language)
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(self.settings["editor_scheme"])
        if scheme:
            buffer.set_style_scheme(scheme)
        buffer.begin_not_undoable_action()
        buffer.set_text(content)
        buffer.end_not_undoable_action()
        buffer.set_modified(False)
        view = GtkSource.View.new_with_buffer(buffer)
        view.set_monospace(True)
        view.override_font(Pango.FontDescription.from_string(self.settings["font"]))
        view.set_show_line_numbers(True)
        view.set_highlight_current_line(True)
        view.set_auto_indent(True)
        view.set_tab_width(4)
        page = scrolled(view)
        caption = label(path.name)
        tab = Gtk.Box(spacing=6)
        tab.pack_start(caption, False, False, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda _: self.guard(lambda: self.close_tab(page)))
        tab.pack_start(close, False, False, 0)
        self.documents[page] = {"path": path, "buffer": buffer, "view": view, "revision": revision}
        buffer.connect("modified-changed", lambda item: caption.set_text(
            path.name + (" •" if item.get_modified() else "")))
        self.tabs.append_page(page, tab)
        tab.show_all()
        page.show_all()
        self.tabs.set_current_page(self.tabs.page_num(page))
        view.grab_focus()

    def document(self):
        return self.documents.get(self.tabs.get_nth_page(self.tabs.get_current_page()))

    def update_status(self):
        doc = self.document()
        if doc:
            self.status.set_text(str(doc["path"]))
        return False

    @staticmethod
    def text(buffer):
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def save(self):
        doc = self.document()
        if doc and doc["buffer"].get_modified():
            doc["revision"] = core.save_document(doc["path"], self.text(doc["buffer"]), doc["revision"])
            doc["buffer"].set_modified(False)
            self.status.set_text(f"Saved {doc['path'].name}")

    def close_tab(self, page=None):
        if page is None:
            page = self.tabs.get_nth_page(self.tabs.get_current_page())
        doc = self.documents.get(page)
        if doc and (not doc["buffer"].get_modified() or self.confirm("Discard unsaved edits in this tab?")):
            self.tabs.remove_page(self.tabs.page_num(page))
            del self.documents[page]

    def on_close(self, *_):
        if self.busy.intersection({"git", "sync", "comment"}):
            self.status.set_text("Wait for the active write or transfer to finish.")
            return True
        if any(doc["buffer"].get_modified() for doc in self.documents.values()):
            if not self.confirm("Discard all unsaved edits and quit?"):
                return True
        self.pool.shutdown(wait=False, cancel_futures=True)
        return False

    def find(self):
        doc = self.document()
        if not doc:
            return
        response, (query,) = self.ask("Find in editor", [("Text", "")])
        if response != Gtk.ResponseType.OK or not query:
            return
        buf = doc["buffer"]
        cursor = buf.get_iter_at_mark(buf.get_insert())
        flags = Gtk.TextSearchFlags.TEXT_ONLY
        match = cursor.forward_search(query, flags, None) or buf.get_start_iter().forward_search(query, flags, None)
        if match:
            start, end = match
            buf.select_range(end, start)
            doc["view"].scroll_to_iter(start, 0.1, False, 0, 0)
        else:
            self.status.set_text("No match")

    def start_shell(self):
        if self.shell_active:
            self.terminal.grab_focus()
            return
        shell = os.environ.get("SHELL") or "/bin/sh"
        self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, str(self.root), [shell], None,
                                  GLib.SpawnFlags.DEFAULT, None, None, -1, None, self.shell_started, None)
        self.shell_active = True

    def shell_started(self, terminal, _pid, error, *_):
        if terminal is not None and error:
            self.shell_active = False
            self.status.set_text(str(error))

    def shell_exited(self, *_):
        self.shell_active = False
        self.status.set_text("Shell exited. Select Start shell to reopen.")

    def show_dock(self, index):
        self.dock.show()
        self.dock.set_current_page(index)

    def toggle_terminal(self):
        if self.dock.get_visible() and self.dock.get_current_page() == 0 and self.terminal.has_focus():
            self.dock.hide()
        else:
            self.show_dock(0)
            self.start_shell()
            self.terminal.grab_focus()

    @staticmethod
    def transcript():
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, left_margin=8, right_margin=8)
        return scrolled(view), view.get_buffer()

    def background(self, name, work, success, failure=None):
        if name in self.busy:
            return False
        self.busy.add(name)
        future = self.pool.submit(work)

        def finish():
            self.busy.discard(name)
            try:
                success(future.result())
            except Exception as exc:
                if failure:
                    failure(str(exc))
                else:
                    self.status.set_text(str(exc))
            return False

        future.add_done_callback(lambda _: GLib.idle_add(finish))
        return True

    def operation(self, name, args):
        if self.busy.intersection({"git", "sync"}):
            self.status.set_text("A Git or sync operation is already running.")
            return
        root = self.root
        self.show_dock(1)
        self.output.set_text("Running…")
        self.background(name, lambda: core.run(args, root),
                        lambda result: self.output.set_text(result or "Done."),
                        lambda error: self.output.set_text(error))

    def git(self, action):
        if action == "pull" and any(doc["buffer"].get_modified() for doc in self.documents.values()):
            raise ValueError("Save modified tabs before pulling.")
        if action in {"pull", "push"} and not self.confirm(f"Run Git {action} in {self.root}?"):
            return
        self.operation("git", core.git_args(action))

    def cloud(self):
        response, (remote, initial) = self.ask(
            "Cloud Sync · rclone bisync", [("Remote folder", self.settings.get("remote", "")),
                                          ("Initial resync (yes/no)", "no")],
            [("Preview", 1), ("Run Sync", 2)])
        if response not in {1, 2}:
            return
        if initial.lower() not in {"yes", "no"}:
            raise ValueError("Use yes or no for initial resync.")
        first = initial.lower() == "yes"
        args = core.sync_args(self.root, remote.strip(), first, response == 1)
        if response == 2 and any(doc["buffer"].get_modified() for doc in self.documents.values()):
            raise ValueError("Save modified tabs before syncing.")
        if response == 2 and not self.confirm("Apply two-way sync? Remote changes and deletions can propagate."):
            return
        self.operation("sync", args)

    def open_in_ide(self, name):
        doc = self.document()
        if doc and doc["buffer"].get_modified():
            raise ValueError("Save the active tab before opening it in your IDE.")
        core.open_ide(name, self.root, doc["path"] if doc else None)

    def show_chat(self):
        if getattr(self, "chat", None):
            self.chat.present()
            return
        self.chat = Gtk.Window(title="ForgePane · Local AI", transient_for=self.window,
                               destroy_with_parent=True, resizable=True)
        self.chat.set_default_size(self.settings["chat_width"], self.settings["chat_height"])
        self.chat.connect("delete-event", lambda window, _: window.hide_on_delete())
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.chat.add(layout)
        transcript, self.chat_log = self.transcript()
        layout.pack_start(transcript, True, True, 0)
        self.model = Gtk.Entry(text=self.settings["model"], placeholder_text="Installed Ollama model")
        self.prompt = Gtk.Entry(placeholder_text="Ask your local model…")
        self.attach = Gtk.CheckButton(label="Attach active editor contents")
        self.prompt.connect("activate", lambda _: self.guard(self.send_chat))
        layout.pack_start(self.model, False, False, 0)
        layout.pack_start(self.attach, False, False, 0)
        layout.pack_start(self.prompt, False, False, 0)
        self.button(layout, "Send", self.send_chat)
        self.chat.show_all()

    def send_chat(self):
        model, prompt = self.model.get_text().strip(), self.prompt.get_text().strip()
        if "chat" in self.busy:
            return
        if not model or not prompt:
            self.chat_log.insert(self.chat_log.get_end_iter(), "Enter an installed model name and a prompt.\n")
            return
        doc = self.document()
        message = prompt
        if self.attach.get_active() and doc:
            source = self.text(doc["buffer"])
            if len(source) > 32000:
                self.chat_log.insert(self.chat_log.get_end_iter(), "The active editor exceeds 32,000 characters.\n")
                return
            message += f"\n\nFile: {doc['path'].name}\n{source}"
        messages = [*self.chat_history[-10:], {"role": "user", "content": message}]
        payload = {"model": model, "stream": False, "messages": messages}
        self.chat_log.insert(self.chat_log.get_end_iter(), f"You: {prompt}\n")
        self.prompt.set_text("")

        def success(result):
            reply = result["message"]["content"]
            self.chat_history = [*messages, {"role": "assistant", "content": reply}]
            self.chat_log.insert(self.chat_log.get_end_iter(), f"AI: {reply}\n\n")

        self.background("chat", lambda: core.local_json(11434, "/api/chat", payload), success,
                        lambda error: self.chat_log.insert(self.chat_log.get_end_iter(), f"Error: {error}\n\n"))

    def connect_review(self):
        response, (port, room, author) = self.ask("Review session", [
            ("Local / SSH tunnel port", str(self.review_port or 8765)),
            ("Shared project ID", getattr(self, "room", self.root.name)),
            ("Display name", getattr(self, "author", os.environ.get("USER", "")))])
        if response == Gtk.ResponseType.OK:
            port = int(port)
            if not 1 <= port <= 65535 or not room.strip() or not 0 < len(author.strip()) <= 80:
                raise ValueError("Enter a valid port, project ID, and display name.")
            self.review_port, self.room, self.author = port, room.strip(), author.strip()
            self.review_request()

    def review_key(self):
        doc = self.document()
        if not doc or not doc["path"].is_relative_to(self.root):
            return None
        path = doc["path"].relative_to(self.root).as_posix()
        revision = core.digest(self.text(doc["buffer"]).encode())
        return json.dumps([self.room, path, revision], ensure_ascii=False)

    def poll_review(self):
        if self.review_port and self.dock.get_visible() and self.dock.get_current_page() == 2:
            self.guard(self.review_request)
        return True

    def review_request(self, post=False):
        if not self.review_port:
            if post:
                self.status.set_text("Connect a review session first.")
            return
        key = self.review_key()
        if not key:
            self.review_text.set_text("Open a file inside the workspace to see its review.")
            return
        port = self.review_port
        data = {"key": key}
        if post:
            note = self.review_note.get_text().strip()
            if not note or len(note) > 3000:
                raise ValueError("Enter a comment of up to 3,000 characters.")
            buf = self.document()["buffer"]
            line = buf.get_iter_at_mark(buf.get_insert()).get_line() + 1
            data.update(author=self.author, note=note, line=line)

        def show(rows):
            if self.review_port == port and self.review_key() == key:
                self.review_text.set_text("\n\n".join(f"L{line} · {author}\n{note}"
                    for author, line, note in rows) or "No comments for this file revision.")
            if post and self.review_note.get_text().strip() == note:
                self.review_note.set_text("")

        endpoint = "/comment" if post else "/comments"
        self.background("comment" if post else "review", lambda: core.local_json(port, endpoint, data),
                        show, lambda error: self.review_text.set_text(f"Review unavailable: {error}"))

    def open_settings(self):
        core.CONFIG.mkdir(parents=True, exist_ok=True)
        path = core.CONFIG / "settings.json"
        if not path.exists():
            core.atomic_write(path, json.dumps(self.settings, indent=2).encode() + b"\n")
        css = core.CONFIG / "theme.css"
        if not css.exists():
            core.atomic_write(css, b"/* GTK CSS overrides. Restart ForgePane to apply. */\n")
        self.open_file(path)
        self.open_file(css)

    def load_plugins(self):
        for name in self.settings["enabled_plugins"]:
            try:
                if not name.isidentifier():
                    raise ValueError("Plugin names must be Python identifiers.")
                path = CONFIG_PLUGINS / f"{name}.py"
                spec = importlib.util.spec_from_file_location(f"forgepane_plugin_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.register(self)
            except Exception as exc:
                self.status.set_text(f"Plugin {name}: {exc}")

    def add_plugin_action(self, name, callback, shortcut=""):
        self.command(self.plugins, name, lambda: callback(self), shortcut)

    def selected_text(self):
        doc = self.document()
        if doc:
            buf = doc["buffer"]
            bounds = buf.get_selection_bounds()
            if bounds:
                return buf.get_text(*bounds, True)
        return ""


CONFIG_PLUGINS = core.CONFIG / "plugins"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, nargs="?", default=Path.cwd())
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error("folder must be an existing directory")
    app = Gtk.Application(application_id="dev.forgepane.Workspace",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", lambda instance: Workspace(instance, args.folder.resolve()))
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
