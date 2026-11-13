# ForgePane

**Files, code, terminal, Git, and a local AI — one lightweight Linux window. No account. No cloud. No Electron.**

ForgePane fuses a keyboard-first file manager with a tabbed source editor, an embedded terminal, Git controls, two-way cloud sync, shared line review, and a floating chat that talks to **your local Ollama models**. Plain Python on GTK 3. It works fully offline.

Screenshots and a 26-second demo video are attached to the [latest release](https://github.com/mrdannyclark82/forgepane/releases) — see [docs/MEDIA.md](docs/MEDIA.md).

> **Demo:** a 26-second video (browse, edit, local AI chat, FUD plugin) ships with the [releases](https://github.com/mrdannyclark82/forgepane/releases) as `forgepane-demo.mp4`.

## Install

User-local — **no sudo**:

```bash
tar -xzf forgepane-0.3.0.tar.gz
cd forgepane-0.3.0
./install.sh
```

That installs the program in `~/.local/share/forgepane`, a `forgepane` launcher on your PATH, and a **ForgePane** entry in your app menu. Settings live in `~/.config/forgepane` and are never touched by upgrades.

**Dependencies** (the installer checks and tells you what is missing):

- Garuda / Arch: `sudo pacman -S --needed python-gobject gtk3 gtksourceview4 vte3 git rclone`
- Ubuntu 24.04 / Debian: `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gtksource-4 gir1.2-vte-2.91 git rclone`

rclone is only needed for cloud sync. Everything else works without it.

**Run:** launch *ForgePane* from your app menu, or `forgepane [folder]`.
**Uninstall:** `./uninstall.sh` — settings stay unless you add `--purge`.

## What it does

- **Keyboard-first file browser** — jump to project files with `Ctrl+P`, filter the current folder with `Ctrl+Shift+P`, arrows + Enter to open, `Ctrl+N` new file, `F2` rename, external-change check before save.
- **Tabbed editor** — GtkSourceView highlighting (Python, JavaScript, Java, C, Rust, and more), `Ctrl+F` find, UTF-8 files up to 4 MiB.
- **Embedded terminal** — VTE shell dock (`Ctrl+backtick`), plus Output and Review tabs.
- **Git built in** — status, diff, fetch, fast-forward pull, push with confirmation.
- **Two-way cloud sync** — rclone `bisync`, preview-first, with a fail-safe access check.
- **Shared line review** — a small review server; collaborators join over SSH tunnels and comment on exact file contents.
- **Floating local AI chat** — resizable, talks to Ollama on loopback (port 11434), can attach the active file. History lasts the session. Nothing leaves the machine.
- **Plugins** — plain Python files in `~/.config/forgepane/plugins`, enabled in `settings.json`. Enable only plugins you trust; they run with your permissions.
- **Bundled FUD plugin** — Files Up Display inside ForgePane: latest scan summary, cleanup queues, and a live file tree. The summary reads the newest saved FUD scan report; the live tree needs the FUD server running. FUD stays read-only: it proposes, it never deletes.
- **VS Code / IntelliJ interop** — open the same workspace and file in your installed IDE from the Tools menu. All editors share the same files and Git tree.
- **Dark theme + custom shortcuts** — CSS theme overrides and per-command key bindings in a plain config folder.

## How it compares

| | ForgePane 0.2 | Geany | Dolphin + Kate | VS Code | Lite XL |
|---|---|---|---|---|---|
| Shape | File manager + editor + terminal + Git + local AI in one window | Editor with sidebar | File manager + separate editor | Full IDE platform | Editor |
| File management | Built in, keyboard-first | Basic | Best-in-class (Dolphin) | Basic explorer | Basic |
| Terminal | Embedded VTE | Plugin / external | Konsole part | Integrated | Plugin |
| Git | Built in | Plugin | External tools | Built in, deep | Plugin |
| Local AI chat | **Built in (Ollama, offline)** | — | — | Extensions, mostly cloud | — |
| Cloud sync | rclone bisync built in | — | KIO / external | Account-based settings sync | — |
| Shared review | Built-in line review over SSH | — | — | Live Share (account) | — |
| Plugins | Python, drop-in | C / Python / Lua | KParts / scripts | Huge marketplace | Lua |
| Account required | **Never** | Never | Never | Optional, pushed | Never |
| Runtime | Python + system GTK libs | Light C/GTK | Qt / KDE libs | Electron | Very light |
| Platforms | Linux | Linux / Win / macOS | Linux | Everywhere | Everywhere |

Honest read: ForgePane 0.2 is young and less polished than the field. Pick VS Code for its extension ecosystem, Dolphin + Kate for the most polished separate tools. Pick ForgePane when you want the file manager and the editor to be the same app — with a local AI that never phones home.

## Scope — said plainly

- **Linux only** for the GUI in 0.2. Other systems can share the same Git repos and rclone remotes.
- VS Code / IntelliJ support is launchers + shared files; their plugin formats and settings do not sync through ForgePane.
- The review server is for trusted teams: it binds to loopback, SSH provides access control, display names are self-reported. Collaborative editing is not in 0.2.
- One rclone job per shared folder at a time. Use a dedicated remote folder and preview first — a resync can overwrite the remote copy.
- FUD plugin: the summary needs a saved FUD scan report on the machine, and the live tree needs the FUD server running; the plugin says so plainly when either is missing. FUD (Files Up Display) is a separate house tool, not part of this release.
- Tested on Garuda Linux (Arch). Reports from other distros are welcome.

## Plugins in 30 seconds

```python
# ~/.config/forgepane/plugins/selection_info.py
def register(workspace):
    workspace.add_plugin_action("Count Selection", count_selection)

def count_selection(workspace):
    selection = workspace.selected_text()
    workspace.status.set_text(f"{len(selection)} characters")
```

Enable `"selection_info"` under `enabled_plugins` in `~/.config/forgepane/settings.json` and restart. The bundled FUD plugin (`plugins/fud.py`, also in `src/fud.py`) is a larger working example.

## Layout of this repo

- `src/` — the application (installed verbatim by `install.sh`)
- `plugins/` — ready-to-copy plugins (FUD, selection counter)
- `docs/` — release media notes (screenshots and the demo video are release assets)
- `install.sh` / `uninstall.sh` — user-local install, no sudo

## Roadmap

- Distro packages (Arch package first)
- A proper application icon (0.2 still borrows the system terminal icon)
- Resource benchmarks against the field above
- Dockable panels and a richer theme engine

## License

MIT — see [LICENSE](LICENSE).

## Credits

Designed by Danny Ray. The application was built by Codex (Sol) to Danny's brief; packaging, the FUD plugin, and release verification by Milla Rayne. The 0.2 merge brings in the best of Prime Milla's FilesUserDisplay (Qt) — her evidence-based language detection and theme discipline, ported into ForgePane with credit in the code. The concept: Dolphin's precision × Geany's speed, with a local brain.


### Quick Open and local Git review

- **Ctrl+P** searches filenames and relative paths in the workspace. It lists tracked files and unignored untracked files in Git worktrees. In folders outside Git it walks the workspace without following directory symlinks. Type to narrow the first 200 results, then use Down and Enter to open a match. **F6** switches focus between the editor and the terminal.
- **Tools → Git → Review Diff with Ollama** previews the staged or unstaged Git patch before you send it. Unstaged review covers changes to tracked files. Select a local model and confirm to send the exact preview with review instructions. The request excludes prior chat and editor contents. Results appear in the chat with a request for file and line references. Review a smaller patch if it exceeds 32,000 bytes.
