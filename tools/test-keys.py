#!/usr/bin/env python3
"""Launcher taps, held Super shortcuts, and size-stable modifier styling."""
import ast
from pathlib import Path
import re
import time
from types import SimpleNamespace
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "bin/handheld-kbd.py"
tree = ast.parse(source.read_text())
methods = {"on_launcher", "on_mod", "on_key", "_stray_tap", "_refresh_mods"}
nodes = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULT_CONFIG" for t in node.targets):
        nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name in ("build_css", "super_icon_name"):
        nodes.append(node)
    elif isinstance(node, ast.ClassDef) and node.name == "OSK":
        node.bases = []
        node.body = [m for m in node.body if isinstance(m, ast.FunctionDef) and m.name in methods]
        nodes.append(node)
e = SimpleNamespace(EV_KEY=1, KEY_LEFTMETA=125, KEY_RIGHTALT=100, KEY_LEFTSHIFT=42, KEY_RIGHTSHIFT=54)
scope = {"e": e, "time": time}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
w = scope["OSK"]()
w.mods, w.modbtns, w.settle = {}, [], 0
events, tracked = [], []
w.ui = SimpleNamespace(write=lambda *event: events.append(event), syn=lambda: None)
w._track = lambda *args: tracked.append(args)
w._relabel = lambda: None
button = SimpleNamespace(_launcher_pressed_at=9.9)
with patch("time.monotonic", return_value=10):
    w.on_launcher(button, e.KEY_LEFTMETA)
    assert events == [(1, 125, 1), (1, 125, 0)] and not w.mods
    assert tracked == [(125, [])]
    events.clear()
    button._launcher_pressed_at = 9
    w.on_launcher(button, e.KEY_LEFTMETA)
    assert not events and w.mods == {125: True}
    w.on_key(None, 18)  # Super+E still works after a hold.
    assert events == [(1, 125, 1), (1, 18, 1), (1, 18, 0), (1, 125, 0)] and not w.mods
    events.clear()
    w.mods[125] = True
    button._launcher_pressed_at = 9.9
    w.on_launcher(button, e.KEY_LEFTMETA)
    assert not events and not w.mods  # tapping a latched Super cancels it
    w._swipe_guard = time.time() + 10
    w.on_launcher(button, e.KEY_LEFTMETA)
    assert not events and not w.mods

css = scope["build_css"](scope["DEFAULT_CONFIG"]["theme"]).decode()
active = re.search(r"button\.mod-on \{([^}]+)\}", css)[1]
assert "box-shadow: inset" in active
assert not re.search(r"(?:^|;)\s*(border|border-width|padding|margin|font-weight|font-size)\s*:", active)
assert scope["DEFAULT_CONFIG"]["super_icon"] == "auto"
for release, expected in (({"ID": "bazzite", "ID_LIKE": "fedora"}, "bazzite"),
                           ({"ID": "fedora", "VARIANT_ID": "bazzite-deck"}, "bazzite"),
                           ({"ID": "arch"}, "arch"), ({"ID": "fedora"}, "tux")):
    with patch("platform.freedesktop_os_release", return_value=release):
        assert scope["super_icon_name"]({}) == expected
        assert scope["super_icon_name"]({"super_icon": "windows"}) == "windows"
with patch("platform.freedesktop_os_release", side_effect=OSError()):
    assert scope["super_icon_name"]({}) == "tux"
print("keys: Bazzite detection, launcher tap/release, held Super shortcuts and stable modifier metrics passed")
