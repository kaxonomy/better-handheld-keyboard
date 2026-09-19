#!/usr/bin/env python3
"""Geometry/config checks without a display, GTK, evdev, or /dev/uinput."""
import ast
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "bin/handheld-kbd.py"
tree = ast.parse(source.read_text())
methods = {"_dock_rect", "_slot_rect", "_persist", "_finish_move", "_debug",
           "set_reported_geometry", "next_geometry", "_on_drag_begin",
           "_on_drag_update", "_on_drag_end", "_prepare_hhd_trigger"}
nodes = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEFAULT_CONFIG" for t in node.targets):
        nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name == "_deep_merge":
        nodes.append(node)
    elif isinstance(node, ast.ClassDef) and node.name == "OSK":
        node.bases = []
        node.body = [m for m in node.body if isinstance(m, ast.FunctionDef) and m.name in methods]
        nodes.append(node)
scope = {"json": json, "os": os, "sys": sys, "time": time,
         "Gtk": SimpleNamespace(EventSequenceState=SimpleNamespace(CLAIMED=1))}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
OSK = scope["OSK"]

with tempfile.TemporaryDirectory() as config_dir:
    scope["CFG_DIR"] = config_dir
    original = {"opacity": 0.42, "locale": "gb", "geometry": {"x": -800, "y": 50, "w": 640, "h": 300}}
    cfg = scope["_deep_merge"](scope["DEFAULT_CONFIG"], original)
    assert cfg["dock_bottom_margin"] == 0 and cfg["opacity"] == 0.42
    w = OSK()
    w.cfg = cfg
    out = {"x": -1280, "y": 80, "w": 1280, "h": 744, "internal": True}
    w._outputs = lambda: [out]
    for level in range(4):
        w.size_level = level
        for margin in (0, 20, 40, 80):
            w.cfg["dock_bottom_margin"] = margin
            g = w._dock_rect(out, level > 0)
            assert g["y"] + g["h"] == out["y"] + out["h"] - margin
            assert g["x"] == -1280 and g["w"] == 1280
    w.cfg["position_mode"] = "custom"
    assert w._slot_rect(original["geometry"], True) == original["geometry"]

    # GTK offsets are local to a surface that itself moves. The second event must
    # use the newly reported frame origin, and touch-up must not apply it twice.
    w.unlocked = True
    w._finishing_move = False
    w._pending_geometry = None
    w.reported_rect = None
    w.set_reported_geometry("-800,400,640,300")
    widget = SimpleNamespace(translate_coordinates=lambda target, x, y: (x, y))
    gesture = SimpleNamespace(get_widget=lambda: widget, set_state=lambda state: None)
    w._on_drag_begin(gesture, 300, 15, "move")
    w._on_drag_update(gesture, 20, -100)
    assert json.loads(w.next_geometry()) == {"x": -780, "y": 300, "w": 640, "h": 300}
    assert w.next_geometry() == ""
    w.set_reported_geometry("-780,300,640,300")
    w._on_drag_update(gesture, 10, -10)
    assert json.loads(w.next_geometry())["y"] == 290
    w.set_reported_geometry("-770,290,640,300")
    w._on_drag_end(gesture, 10, -10)
    assert w.next_geometry() == ""

    w._on_drag_begin(gesture, 0, 0, "nw")
    w._on_drag_update(gesture, -20, -30)
    rect = json.loads(w.next_geometry())
    assert rect == {"x": -790, "y": 260, "w": 660, "h": 330}
    w.set_reported_geometry("-790,260,660,330")
    Path(config_dir, "config.json").write_text(json.dumps(original))
    w._finishing_move = True
    w._reload_kwin_script = lambda **kwargs: None
    # Ordinary in-flight frame reports must not satisfy the explicit Done snapshot.
    assert w._finish_move() is True
    w.set_reported_geometry("final:-790,260,660,330")
    assert w._finish_move() is True       # wait for asynchronous configure to settle
    w._geometry_changed_at = time.monotonic() - 1
    assert w._finish_move() is False
    persisted = json.loads(Path(config_dir, "config.json").read_text())
    assert persisted["geometry"] == rect and persisted["position_mode"] == "custom"
    assert persisted["opacity"] == 0.42 and persisted["locale"] == "gb"
    restored = scope["_deep_merge"](scope["DEFAULT_CONFIG"], persisted)
    w.cfg = restored
    assert w._slot_rect(restored["geometry"], True) == rect
    w.set_reported_geometry("invalid")
    assert w.reported_rect == rect

    rules = Path(config_dir, "kwinrulesrc")
    rules.write_text("\n".join(
        "[6c4263a8-3263-4d41-85f7-75c704113ed%s]\nDescription=Better Handheld Keyboard — Steam keyboard\nwmclass=steam\n" % suffix
        for suffix in "bcd"))
    commands, timers, started = [], [], []
    scope["subprocess"] = SimpleNamespace(run=lambda args, **kwargs: commands.append(args))
    scope["GLib"] = SimpleNamespace(timeout_add=lambda delay, callback: timers.append((delay, callback)))
    with patch("os.path.expanduser", return_value=str(rules)):
        assert w._prepare_hhd_trigger(lambda: started.append(True))
        assert len(commands) == 10 and commands[-1][0] == "qdbus6"
        assert timers[0][0] == 300 and not started
        assert timers[0][1]() is False and started == [True]
        rules.write_text("")
        with redirect_stderr(io.StringIO()) as log:
            assert not w._prepare_hhd_trigger(lambda: started.append(True))
        assert "protection unavailable" in log.getvalue()
        assert len(commands) == 10 and len(timers) == 1

print("movement/config: margins, move/resize, release, persistence, migration, trigger startup protection passed")
