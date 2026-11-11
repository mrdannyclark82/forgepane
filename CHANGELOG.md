# Changelog

## 0.2.0 — 2026-11-11

The merge release: the best of both DevFile builds in one app.

- **Language detection by evidence, not just filename** — ported from Prime
  Milla's FilesUserDisplay (her EditorPane detection order and language
  table, re-implemented for GtkSource): strong filenames (Dockerfile,
  Makefile, CMakeLists.txt, .env, .bashrc, Cargo.toml), then the shebang
  line, then content patterns, then the extension. Extensionless scripts
  and config files that opened un-highlighted in 0.1 now light up.
- **Theme palette named by role** — Prime's ThemeEngine discipline applied
  to ForgePane's own colors: one palette, one place, every value named by
  the role it plays (window, panel, header, accent, selection).
- Everything from 0.1.0 unchanged otherwise: tabs, git, rclone preview-first
  sync, review server, Ollama chat, plugins, FUD plugin.

## 0.1.0 — 2026-11-10
## 0.1.0 — 2026-11-10

First public release.

- Keyboard-first file browser, tabbed GtkSourceView editor, VTE terminal dock
- Git status/diff/fetch/fast-forward pull/push with confirmation
- Two-way rclone bisync cloud sync with preview-first discipline
- Shared line review server for trusted teams (loopback + SSH tunnels)
- Floating local AI chat via Ollama on loopback (port 11434); no account, works offline
- Python plugin system; ships with the FUD (Files Up Display) plugin
- VS Code / IntelliJ launchers; all editors share the same files and Git tree
- User-local installer (no sudo): app menu entry, PATH launcher, clean uninstall
