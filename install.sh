#!/bin/sh
# ForgePane — user-local install (no sudo). Idempotent: safe to re-run
# for upgrades; your settings in ~/.config/forgepane are never touched.
#
# Layout (XDG user dirs):
#   app:       ~/.local/share/forgepane/        (program files)
#   launcher:  ~/.local/bin/forgepane
#   menu:      ~/.local/share/applications/dev.forgepane.Workspace.desktop
#   config:    ~/.config/forgepane/             (created by the app itself)
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR="$HOME/.local/share/forgepane"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "ForgePane install (user-local)"
missing=""
for pkg in python3; do command -v $pkg >/dev/null 2>&1 || missing="$missing $pkg"; done
python3 - <<'PY' || missing="$missing python-gobject/gtk3/gtksourceview4/vte3"
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "4")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, GtkSource, Vte  # noqa: F401
PY
if [ -n "$missing" ]; then
  echo "Missing runtime pieces:$missing" >&2
  echo "Garuda/Arch:  sudo pacman -S --needed python-gobject gtk3 gtksourceview4 vte3 git rclone" >&2
  echo "Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gtksource-4 gir1.2-vte-2.91 git rclone" >&2
  exit 1
fi
command -v rclone >/dev/null 2>&1 || echo "note: rclone not found — cloud sync stays unavailable until you install it (everything else works)."

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
# Replace program files only; never the user's config directory.
rm -f "$APP_DIR"/*.py "$APP_DIR"/*.css "$APP_DIR"/*.md "$APP_DIR"/*.desktop \
      "$APP_DIR"/forgepane
rm -rf "$APP_DIR/plugins"
cp -r "$SRC/src/." "$APP_DIR/"
mv "$APP_DIR/launcher" "$APP_DIR/forgepane"
cp "$SRC/dev.forgepane.Workspace.desktop" "$APP_DIR/"
chmod +x "$APP_DIR/forgepane"
ln -sf "$APP_DIR/forgepane" "$BIN_DIR/forgepane"
cp "$SRC/dev.forgepane.Workspace.desktop" "$DESKTOP_DIR/"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
echo "Installed: $APP_DIR"
echo "Launch from your app menu (ForgePane) or run: forgepane [folder]"
echo "Uninstall: $SRC/uninstall.sh   (your settings in ~/.config/forgepane stay unless you add --purge)"
