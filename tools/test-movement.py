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
           "_on_drag_update", "_on_drag_end", "_prepare_hhd_trigger",
           "finish_movement", "_request_final_geometry", "_complete_movement"}
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
scope = {"DBUS": str(source.with_name("handheld-kbd-dbus")), "json": json, "os": os, "sys": sys, "time": time,
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
    w._move_finished_callback = None
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

    # Closing without Done must wait for the final queued motion and save the
    # compositor's result before unmapping. Reopening and restarting use that rect.
    callbacks, hidden, snapshots = [], [], []
    scope["GLib"] = SimpleNamespace(timeout_add=lambda delay, callback: callbacks.append(callback))
    w._apply_handle = lambda: None
    w.request_geometry = lambda: snapshots.append(True)
    w.unlocked = True
    w.cfg["position_mode"] = "bottom"
    final = {"x": -600, "y": 110, "w": 660, "h": 330}
    w._pending_geometry = final
    def hide():
        saved = json.loads(Path(config_dir, "config.json").read_text())
        assert saved["geometry"] == final and saved["position_mode"] == "custom"
        hidden.append(True)
    w.finish_movement(hide)
    assert not hidden and not w.unlocked and w._finishing_move
    assert callbacks[0]() is True and not snapshots  # move still queued
    assert callbacks[1]() is True and not hidden
    assert json.loads(w.next_geometry()) == final
    w.set_reported_geometry("-600,110,660,330")
    assert callbacks[0]() is False and snapshots == [True]
    assert callbacks[1]() is True and not hidden  # ordinary report is not the snapshot
    w.set_reported_geometry("final:-600,110,660,330")
    w._geometry_changed_at = time.monotonic() - 1
    assert callbacks[1]() is False and hidden == [True]
    assert callbacks[1]() is False and hidden == [True]  # stale timers cannot hide twice
    restored = scope["_deep_merge"](scope["DEFAULT_CONFIG"], json.loads(Path(config_dir, "config.json").read_text()))
    w.cfg = restored
    assert w._slot_rect(restored["geometry"], False) == final
    w.finish_movement(lambda: hidden.append(True))
    assert hidden == [True, True]  # a normal hide has no geometry delay

    # A failed one-shot request retains the latest actual frame, not the old dock.
    w.unlocked = True
    w.finish_movement()
    w._finish_tries = 20
    with redirect_stderr(io.StringIO()) as log:
        assert w._finish_move() is False
    assert "saving last KWin frame" in log.getvalue()
    assert json.loads(Path(config_dir, "config.json").read_text())["geometry"] == final

    rules = Path(config_dir, "kwinrulesrc")
    rules.write_text("\n".join(
        "[6c4263a8-3263-4d41-85f7-75c704113ed%s]\nDescription=Better Handheld Keyboard — Steam keyboard\nwmclass=steam\n" % suffix
        for suffix in "bcd"))
    commands, timers, started = [], [], []
    scope["subprocess"] = SimpleNamespace(run=lambda args, **kwargs: commands.append(args))
    scope["GLib"] = SimpleNamespace(timeout_add=lambda delay, callback: timers.append((delay, callback)))
    with patch("os.path.expanduser", return_value=str(rules)):
        assert w._prepare_hhd_trigger(lambda: started.append(True))
        assert len(commands) == 10 and commands[-1][0].endswith("handheld-kbd-dbus")
        assert timers[0][0] == 300 and not started
        assert timers[0][1]() is False and started == [True]
        rules.write_text("")
        with redirect_stderr(io.StringIO()) as log:
            assert not w._prepare_hhd_trigger(lambda: started.append(True))
        assert "protection unavailable" in log.getvalue()
        assert len(commands) == 10 and len(timers) == 1

# Plasma owns automatic visibility; its delayed deactivation must not hide an M1
# summon, and manually dismissing it must let a second tap on the same field work.
main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
visibility = [n for n in main.body if isinstance(n, ast.FunctionDef)
              and n.name in {"_show", "_hide", "_toggle", "_input_method", "_steam_osk"}]
state = {"shown": False, "automatic": False, "input_method": False, "hiding": None}
timers, requests = [], []
bus = SimpleNamespace(call=lambda *args: requests.append(args))
gio = SimpleNamespace(bus_get_sync=lambda *_: bus, BusType=SimpleNamespace(SESSION=0),
                      DBusCallFlags=SimpleNamespace(NO_AUTO_START=1))
context = {"state": state, "GAMEMODE": False, "_mark_proven": lambda: None,
           "w": SimpleNamespace(ensure_placed=lambda: None, show_all=lambda: None,
                                hide=lambda: None, _debug=lambda text: None,
                                finish_movement=lambda done: done()),
           "_setvis": lambda v: state.update(shown=v == "1"),
           "GLib": SimpleNamespace(timeout_add=lambda ms, fn: timers.append(fn), Error=RuntimeError)}
exec(compile(ast.Module(body=visibility, type_ignores=[]), str(source), "exec"), context)
trigger_ready = [True]
with patch.dict(sys.modules, {"gi.repository": SimpleNamespace(Gio=gio),
                             "handheld_kbd_backend": SimpleNamespace(hhd_trigger_ready=lambda: trigger_ready[0])}):
    context["_input_method"](True)
    assert state["shown"] and state["automatic"]
    context["_input_method"](False)
    context["_input_method"](True)
    timers.pop()()
    assert state["shown"] and state["automatic"]  # moving to a second field has no flicker
    context["_toggle"]()
    assert not state["shown"] and requests[-1][3] == "Dismiss"
    context["_input_method"](True)
    assert not state["shown"]  # stale native visibility cannot undo manual dismissal
    context["_input_method"](False)
    context["_input_method"](True)
    timers.pop()()
    assert state["shown"] and state["automatic"]  # another tap on the same field
    context["_input_method"](False)
    timers.pop()()
    assert not state["shown"]
    context["_show"]()
    context["_input_method"](True)
    context["_input_method"](False)
    timers.pop()()
    assert state["shown"] and not state["automatic"]  # manual M1 ownership preserved

    # Steam and the physical M1 event can arrive in either order. The service
    # decides whether to mirror in the same callback that would change visibility.
    for steam_first in (False, True):
        context["_hide"]()
        if steam_first:
            context["_steam_osk"]("Toggle")
            assert not state["shown"]
        context["_toggle"]("M1")
        context["_input_method"](True)
        if not steam_first:
            context["_steam_osk"]("Toggle")
        context["_input_method"](False)
        timers.pop()()
        assert state["shown"] and not state["automatic"]
        context["_toggle"]("M1")
        assert not state["shown"]
    trigger_ready[0] = False
    context["_steam_osk"]("Toggle")
    assert state["shown"]  # generic mirror/reconnect fallback remains available
    context["_steam_osk"]("Hide")
    assert not state["shown"]

    # Hiding during a drag waits for the compositor snapshot. A subsequent
    # manual show or automatic field change must cancel that pending hide.
    pending = []
    context["w"].finish_movement = pending.append
    context["_show"]()
    context["_hide"]()
    assert state["shown"] and state["hiding"] is not None
    context["_toggle"]("M1")
    pending.pop()()
    assert state["shown"] and state["hiding"] is None
    context["_hide"]()
    pending.pop()()
    assert not state["shown"]
    context["_input_method"](True)
    context["_input_method"](False)
    timers.pop()()
    assert state["hiding"] is not None
    context["_input_method"](True)
    pending.pop()()
    assert state["shown"] and state["automatic"]

print("movement/config: margins, move/resize, persistence, trigger protection, Plasma/M1 visibility passed")
