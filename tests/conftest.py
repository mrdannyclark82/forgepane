import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
