# Changelog

## Unreleased

- **Run the current file and watch its output in the app** — ▶ Run in the
  header, F5, or Ctrl+Enter saves the active tab and runs it. The Output
  tab, which until now only showed Git and sync results after they
  finished, is now a live console: stdout and stderr stream in as the
  program prints (stderr in red), the UI never blocks, and each run gets a
  header with the exact command and a footer with the exit code or signal
  and the duration. The interpreter comes from the #! line, then the
  extension, then content detection (python3, bash/sh, node; ruby, perl,
  lua, php, go run, deno/tsx/ts-node when installed), with a plain message
  when it isn't installed. ■ Stop (Shift+F5) ends the whole process group
  (SIGTERM, then SIGKILL after 1.5 s); Clear is Ctrl+Shift+K; a stdin line
  feeds programs that read input. Output is capped at ~10,000 lines.
- **Shortcut change:** Refresh Files moves from F5 to Ctrl+R so F5 can run
  the file. Either can be rebound under `shortcuts` in settings.json.
- Git and sync results now append to the Output tab under a header line
  instead of replacing its contents.

## 0.3.0 — 2026-11-12

- **Project quick-open (Ctrl+P)** — jump to any file in the project;
  the list comes from `git ls-files --exclude-standard`, so gitignored
  files never appear. The current-folder filter moved to Ctrl+Shift+P.
- **Review Diff** — preview the local Git diff, confirm, and a local
  Ollama model (verified with qwen2.5-coder) reviews it and reports
  findings with file and line. Patches are capped at 32,000 bytes.
- **Fix: startup crash on mixed GTK systems** — Gdk is now pinned to
  3.0 at startup; unpinned, a system that loads Gdk 4 first made the
  app crash before its window opened.


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
