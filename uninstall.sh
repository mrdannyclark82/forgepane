#!/bin/sh
# ForgePane — user-local uninstall. Removes the program and the menu
# entry. Settings in ~/.config/forgepane are KEPT unless --purge is given.
set -eu
APP_DIR="$HOME/.local/share/forgepane"
BIN_LINK="$HOME/.local/bin/forgepane"
DESKTOP_FILE="$HOME/.local/share/applications/dev.forgepane.Workspace.desktop"
rm -rf "$APP_DIR"
rm -f "$BIN_LINK" "$DESKTOP_FILE"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.config/forgepane"
  echo "ForgePane removed, settings purged."
else
  echo "ForgePane removed. Settings kept in ~/.config/forgepane (re-run with --purge to remove them too)."
fi
