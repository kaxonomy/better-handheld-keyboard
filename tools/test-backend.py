#!/usr/bin/env python3
"""Hardware-free checks: python3 tools/test-backend.py."""
import contextlib
import ast
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import handheld_kbd_backend as backend

DMI = {"sys_vendor": "ASUSTeK COMPUTER INC.", "product_name": "ROG Ally X RC72LA_RC72LA",
       "board_vendor": "ASUSTeK COMPUTER INC.", "board_name": "RC72LA"}


def bitmap(*keys):
    bits = struct.calcsize("L") * 8
    value = sum(1 << key for key in keys)
    words = []
    while value:
        words.append(f"{value & ((1 << bits) - 1):x}")
        value >>= bits
    return " ".join(reversed(words))


def input_event(kind, code, value, stamp=1.0):
    return SimpleNamespace(type=kind, code=code, value=value, timestamp=lambda: stamp)


class BackendTests(unittest.TestCase):
    def test_dmi_requires_asus_and_supported_ally(self):
        self.assertTrue(backend.is_ally(DMI))
        self.assertTrue(backend.is_ally({**DMI, "product_name": "ROG Ally X RC72LA_RC72LA_000123206"}))
        self.assertFalse(backend.is_ally({**DMI, "sys_vendor": "Other", "board_vendor": "Other"}))
        self.assertTrue(backend.is_ally({**DMI, "product_name": "ROG Ally RC71L_RC71L", "board_name": "RC71L"}))
        self.assertFalse(backend.is_ally({**DMI, "product_name": "RC72LAX", "board_name": "RC72LAX"}))
        self.assertFalse(backend.is_ally({}))

    def detect(self, hhd_api=False, hhd_process=False, service=False, ip=False, cli=False, config=None, dmi=DMI):
        def command(args):
            if args[0] == "systemctl":
                return "hhd@user.service loaded active running" if service else ""
            return "b true" if ip else "b false"
        with patch.object(backend, "hhd_api_active", return_value=hhd_api), \
             patch.object(backend, "hhd_process_active", return_value=hhd_process), \
             patch.object(backend, "_command", side_effect=command), \
             patch.object(backend, "read_dmi", return_value=dmi), \
             patch.object(backend.shutil, "which", return_value="/usr/bin/inputplumber" if cli else None):
            return backend.detect_backend(config)

    def test_runtime_backend_detection(self):
        for source in ("hhd_api", "hhd_process", "service"):
            with self.subTest(source=source):
                result = self.detect(**{source: True})
                self.assertEqual(result["backend"], "hhd")
                self.assertEqual(result["trigger"], "ally-m1")
                self.assertTrue(result["ally_m1"])
        self.assertEqual(self.detect(ip=True)["backend"], "inputplumber")
        self.assertEqual(self.detect(ip=True, hhd_api=True)["backend"], "hhd")
        self.assertEqual(self.detect(cli=True)["backend"], "generic")
        self.assertEqual(self.detect()["trigger"], "mirror")
        self.assertEqual(self.detect(hhd_api=True, dmi={**DMI, "product_name": "ROG Ally RC71L_RC71L",
                                                       "board_name": "RC71L"})["trigger"], "ally-m1")

    def test_recovery_override_preserves_backend_diagnostic(self):
        result = self.detect(hhd_api=True, config={"input_backend": "generic"})
        self.assertEqual(result["backend"], "hhd")
        self.assertEqual(result["trigger"], "mirror")

    def test_process_detection_ignores_executable_mentions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "1"
            path.mkdir()
            cmdline = path / "cmdline"
            for command in (b"python3\0/usr/bin/hhd\0--user\0user\0", b"hhd\0", b"python3\0-m\0hhd\0",
                            b"python3\0-sP\0/usr/bin/hhd\0"):
                cmdline.write_bytes(command)
                self.assertTrue(backend.hhd_process_active(directory))
            for command in (b"bash\0-c\0hhd --help\0", b"python3\0other.py\0hhd\0", b"hhd-ui\0"):
                cmdline.write_bytes(command)
                self.assertFalse(backend.hhd_process_active(directory))

    def test_stale_api_socket_is_not_active(self):
        self.assertFalse(backend.hhd_api_active("/nonexistent/handheld-kbd-test.socket"))

    def test_diagnostics_prefers_live_trigger_over_tty_runtime_file(self):
        info = self.detect(hhd_api=True)
        with patch.object(backend, "detect_backend", return_value=info), \
             patch.object(backend, "read_runtime_status", return_value={}), \
             patch.object(backend, "ally_devices", return_value=[]), \
             patch.object(backend, "_command", return_value='s "ally-m1"'):
            self.assertTrue(backend.diagnostics()["ready"])
        with patch.object(backend, "detect_backend", return_value=info), \
             patch.object(backend, "read_runtime_status", return_value={"ready": True}), \
             patch.object(backend, "ally_devices", return_value=[]), \
             patch.object(backend, "_command", return_value='s ""'):
            self.assertFalse(backend.diagnostics()["ready"])

    def test_press_debounce_and_filter(self):
        press = backend.M1Press()
        self.assertFalse(press.feed(backend.EV_KEY, 188, 1, 1))  # M2 untouched
        self.assertFalse(press.feed(2, backend.KEY_F17, 1, 1))
        self.assertTrue(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 1))
        self.assertFalse(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 1.2))
        self.assertFalse(press.feed(backend.EV_KEY, backend.KEY_F17, 2, 1.3))
        self.assertFalse(press.feed(backend.EV_KEY, backend.KEY_F17, 0, 1.4))
        self.assertTrue(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 2))
        press.feed(backend.EV_KEY, backend.KEY_F17, 0, 2.01)
        self.assertFalse(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 2.02))
        press.feed(backend.EV_KEY, backend.KEY_F17, 0, 2.03)
        self.assertTrue(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 2.2))
        press.feed(backend.EV_KEY, backend.KEY_F17, 0, 2.3)
        self.assertTrue(press.feed(backend.EV_KEY, backend.KEY_F17, 1, 1))  # clock adjusted backwards


class HotkeyTests(unittest.TestCase):
    def test_m1_is_not_also_delivered_as_an_optional_hotkey(self):
        source = Path(__file__).resolve().parents[1] / "bin/handheld-kbd.py"
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "setup_hotkey")
        for codes, ready in ((["KEY_F17"], True), (["KEY_F17"], False),
                             (["KEY_LEFTMETA", "KEY_F17"], True)):
            watches, toggles = [], []
            devices = {}
            for path in ("/dev/input/ally", "/dev/input/external"):
                devices[path] = SimpleNamespace(path=path, name=path, fd=path,
                    capabilities=lambda: {1: [187, 125]}, read=lambda: iter([
                        input_event(1, 125, 1), input_event(1, 187, 1),
                        input_event(1, 187, 0), input_event(1, 125, 0)]))
            scope = {"e": SimpleNamespace(EV_KEY=1, KEY_F17=187, KEY_LEFTMETA=125), "sys": sys,
                     "GLib": SimpleNamespace(IO_IN=1, io_add_watch=lambda *args: watches.append(args))}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
            with patch.dict(sys.modules, {"evdev": SimpleNamespace(InputDevice=devices.get,
                                                                     list_devices=lambda: list(devices))}), \
                 patch.object(backend, "hhd_trigger_ready", side_effect=lambda path: ready and path.endswith("ally")):
                scope["setup_hotkey"]({"hotkey": codes}, lambda: toggles.append(True))
                for fd, condition, callback, device in watches:
                    callback(fd, condition, device)
            # Only the direct listener's F17 is excluded. A separate keyboard,
            # a chord, and systems without the direct listener retain hotkeys.
            self.assertEqual(len(toggles), 1 if ready and codes == ["KEY_F17"] else 2)


class ShortcutConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HOME": self.temp.name}).start()
        self.command = str(Path(self.temp.name) / ".local/bin/handheld-kbd-toggle")
        Path(self.command).parent.mkdir(parents=True)
        Path(self.command).touch()
        self.component = "net.local.handheld-kbd-toggle.desktop"
        self.f17 = 0x01000040
        self.info = ["_launch", "Keyboard", self.component, "Keyboard", "default", "Default", [], []]
        self.infos, self.calls = [self.info], []
        self.keys = [([self.f17, 0, 0, 0],)]
        self.commandline = self.command
        self.apply = True
        def call(service, path, interface, method, params, result_type, flags, timeout, cancellable):
            self.assertEqual((service, path, interface),
                             ("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"))
            self.calls.append((method, params))
            if method == "globalShortcutsByKey":
                self.assertEqual(params, ("((ai)(i))", (([self.f17, 0, 0, 0],), (0,))))
                value = (self.infos,)
            elif method == "shortcutKeys":
                value = (self.keys,)
            else:
                self.assertEqual(method, "setForeignShortcutKeys")
                self.assertEqual(params[0], "(asa(ai))")
                if self.apply:
                    self.keys = params[1][1]
                value = ()
            return SimpleNamespace(unpack=lambda: value)
        self.bus = SimpleNamespace(call_sync=call)
        gio = SimpleNamespace(BusType=SimpleNamespace(SESSION=0),
                              DBusCallFlags=SimpleNamespace(NO_AUTO_START=1),
                              bus_get_sync=lambda *_: self.bus,
                              DesktopAppInfo=SimpleNamespace(new=lambda name:
                                  SimpleNamespace(get_commandline=lambda: self.commandline)))
        glib = SimpleNamespace(Variant=lambda signature, values: (signature, values))
        self.gio, self.glib = gio, glib
        patch.dict(sys.modules, {"gi.repository": SimpleNamespace(Gio=gio, GLib=glib)}).start()

    def test_only_plain_f17_is_removed_and_other_sequences_survive(self):
        retained = [([self.f17 | 0x02000000, 0, 0, 0],),  # Shift+F17
                    ([self.f17 + 1, 0, 0, 0],),          # M2/F18
                    ([self.f17, 65, 0, 0],)]            # multi-step F17, A
        self.keys += retained
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [self.component])
        self.assertEqual(self.keys, retained)
        self.assertEqual([method for method, _ in self.calls],
                         ["globalShortcutsByKey", "shortcutKeys", "setForeignShortcutKeys", "shortcutKeys"])
        self.assertEqual(self.calls[2][1][1][0], [self.component, "_launch", "Keyboard", "Keyboard"])
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [])
        self.assertEqual(sum(method == "setForeignShortcutKeys" for method, _ in self.calls), 1)

    def test_canonical_home_path_explicit_toggle_and_nondefault_context(self):
        alias = Path(self.temp.name) / "home-alias"
        alias.symlink_to(self.temp.name, target_is_directory=True)
        self.commandline = str(alias / ".local/bin/handheld-kbd-toggle") + " toggle"
        self.info[4] = "custom"
        self.keys = [([self.f17],)]  # accept unpadded QKeySequence too
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [self.component])
        self.assertEqual(self.keys, [])
        self.assertEqual(self.calls[2][1][1][0][0], self.component + "|custom")

    def test_modern_glib_desktop_app_info_namespace(self):
        unix = SimpleNamespace(DesktopAppInfo=self.gio.DesktopAppInfo)
        del self.gio.DesktopAppInfo
        with patch.dict(sys.modules, {"gi.repository": SimpleNamespace(Gio=self.gio, GLib=self.glib, GioUnix=unix)}):
            self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [self.component])
        self.assertEqual(self.keys, [])

    def test_other_commands_and_nonlaunch_actions_are_untouched(self):
        for command in (self.command + " show", self.command + " hide", "/usr/bin/other",
                        "/bin/sh -c " + self.command, '"unterminated'):
            self.commandline = command
            self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [])
        self.commandline = self.command
        self.info[0] = "custom-action"
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [])
        self.info[0], self.info[2] = "_launch", "kwin"
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [])
        self.infos = []
        self.assertEqual(backend.resolve_m1_shortcut_conflicts(), [])
        self.assertTrue(all(method == "globalShortcutsByKey" for method, _ in self.calls))

    def test_absent_api_and_failed_readback_are_reported(self):
        with patch.object(self.bus, "call_sync", side_effect=RuntimeError("service unavailable")):
            with self.assertRaisesRegex(RuntimeError, "service unavailable"):
                backend.resolve_m1_shortcut_conflicts()
        self.apply = False
        with self.assertRaisesRegex(RuntimeError, "did not take effect"):
            backend.resolve_m1_shortcut_conflicts()


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs, self.devroot = self.root / "sys", self.root / "dev"
        self.sysfs.mkdir()
        self.devroot.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.temp.name}).start()

    def device(self, name, vendor="0b05", product="1b4c", keys=(187, 193)):
        root = self.sysfs / name / "device"
        (root / "id").mkdir(parents=True)
        (root / "capabilities").mkdir()
        (root / "id/vendor").write_text(vendor)
        (root / "id/product").write_text(product)
        (root / "capabilities/key").write_text(bitmap(*keys))
        (root / "name").write_text("Asus Keyboard")
        (self.devroot / name).touch()
        return str(self.devroot / name)

    def candidates(self, dmi=DMI):
        return backend.ally_devices(dmi, self.sysfs, self.devroot)

    def test_only_targeted_asus_shortcut_interface_matches(self):
        self.device("event1", vendor="1234")
        self.device("event2", product="1234")
        self.device("event3", keys=(187,))
        expected = self.device("event4")
        self.assertEqual(self.candidates(), [{"path": expected, "name": "Asus Keyboard"}])
        self.assertEqual(self.candidates({}), [])

    def test_original_ally_uses_the_same_shortcut_backend(self):
        expected = self.device("event7", product="1abe")
        dmi = {**DMI, "product_name": "ROG Ally RC71L_RC71L", "board_name": "RC71L"}
        self.assertEqual(self.candidates(dmi), [{"path": expected, "name": "Asus Keyboard"}])
        trigger = self.trigger()
        self.assertEqual(trigger.device.info.product, backend.ALLY_PRODUCT)

    def trigger(self):
        candidate_fn = backend.ally_devices
        self.shortcut_cleanup = patch.object(backend, "resolve_m1_shortcut_conflicts", return_value=[]).start()
        patch.object(backend, "ally_devices", side_effect=lambda dmi: candidate_fn(dmi, self.sysfs, self.devroot)).start()
        self.calls, self.opened, self.removed, self.watches = [], [], [], []
        self.active = []
        owner = self
        class Device:
            def __init__(self, path):
                owner.opened.append(path)
                self.path, self.name, self.fd = path, "Asus Keyboard", 42
                product = int((owner.sysfs / Path(path).name / "device/id/product").read_text(), 16)
                self.info = SimpleNamespace(vendor=backend.ASUS_VENDOR, product=product)
                self.closed, self.events = False, []
            def active_keys(self): return owner.active
            def capabilities(self): return {backend.EV_KEY: [187, 188, 193]}
            def read(self):
                events, self.events = self.events, []
                return iter(events)
            def close(self): self.closed = True
        self.device_class = Device
        self.glib = SimpleNamespace(IO_IN=1, IO_HUP=2, IO_ERR=4, PRIORITY_HIGH=-100,
            io_add_watch=lambda *args: self.watches.append(args) or 1, timeout_add_seconds=lambda *args: 2,
            source_remove=self.removed.append)
        with contextlib.redirect_stderr(io.StringIO()):
            return backend.AllyM1Trigger({"dmi": DMI}, {}, self.glib, Device, lambda *args: self.calls.append("Toggle"))

    def test_disconnect_reconnect_and_reused_event_number(self):
        first = self.device("event5")
        trigger = self.trigger()
        original = trigger.device
        self.assertTrue(backend.read_runtime_status()["ready"])
        self.assertEqual(self.opened, [first])
        Path(first).unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.rescan()
        self.assertTrue(original.closed)
        self.assertFalse(backend.read_runtime_status()["ready"])
        second = self.device("event9")
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.rescan()
        self.assertEqual(trigger.device.path, second)
        self.assertTrue(backend.read_runtime_status()["ready"])
        self.assertIn(1, self.removed)
        # Replacing a node with the same event number also closes and reopens it.
        replacement = self.devroot / "replacement"
        replacement.touch()
        old = trigger.device
        os.replace(replacement, second)
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.rescan()
        self.assertTrue(old.closed)
        self.assertIsNot(old, trigger.device)

    def test_shortcut_cleanup_requires_successful_open_and_runs_on_reconnect(self):
        trigger = self.trigger()
        self.shortcut_cleanup.assert_not_called()
        self.device("event5")
        with patch.object(self.device_class, "__init__", side_effect=PermissionError("input permission")), \
             contextlib.redirect_stderr(io.StringIO()):
            trigger.rescan()
        self.shortcut_cleanup.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.rescan()
            trigger.rescan()
        self.shortcut_cleanup.assert_called_once_with()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.disconnect()
            trigger.rescan()
        self.assertEqual(self.shortcut_cleanup.call_count, 2)

    def test_shortcut_api_failure_does_not_disable_m1(self):
        trigger = self.trigger()
        self.device("event5")
        self.shortcut_cleanup.side_effect = RuntimeError("service unavailable")
        with contextlib.redirect_stderr(io.StringIO()) as log:
            trigger.rescan()
        self.assertIn("could not check/remove duplicate Plasma F17", log.getvalue())
        self.assertIn("service unavailable", log.getvalue())
        self.assertIsNotNone(trigger.device)
        self.assertTrue(backend.read_runtime_status()["ready"])

    def test_events_toggle_only_m1_and_never_write(self):
        self.device("event5")
        self.device("event6", vendor="1234")
        trigger = self.trigger()
        self.assertEqual(len(self.opened), 1)
        event = input_event
        trigger.device.events = [event(1, 188, 1), event(1, 187, 1), event(1, 187, 2), event(1, 187, 1)]
        trigger.on_input(42, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0], "Toggle")
        self.assertEqual(self.watches[0][1], self.glib.PRIORITY_HIGH)
        # Fake evdev has no write, grab, or uinput methods: passively reading is enough.

    def test_slow_show_cannot_turn_queued_bounce_into_two_presses(self):
        self.device("event5")
        trigger = self.trigger()
        trigger.device.events = [input_event(1, 187, 1, 1), input_event(1, 187, 0, 1.01),
                                 input_event(1, 187, 1, 1.02), input_event(1, 187, 0, 1.03)]
        # Placement can block the GLib loop after the first accepted press.
        with patch.object(backend.time, "monotonic", side_effect=[1, 1.3, 1.31, 1.32]):
            trigger.on_input(42, 1)
        self.assertEqual(self.calls, ["Toggle"])

    def test_separate_presses_buffered_during_busy_loop_are_both_delivered(self):
        self.device("event5")
        trigger = self.trigger()
        trigger.device.events = [input_event(1, 187, 1, 1), input_event(1, 187, 0, 1.1),
                                 input_event(1, 187, 1, 2), input_event(1, 187, 0, 2.1)]
        with patch.object(backend.time, "monotonic", return_value=3):
            trigger.on_input(42, 1)
        self.assertEqual(self.calls, ["Toggle", "Toggle"])

    def test_device_identity_rechecked_after_open(self):
        self.device("event5")
        trigger = self.trigger()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.disconnect()
            self.device_class.capabilities = lambda device: {backend.EV_KEY: [187]}
            trigger.rescan()
        self.assertIsNone(trigger.device)
        self.assertFalse(backend.read_runtime_status()["ready"])

    def test_real_glib_m1_precedes_native_request_in_same_batch(self):
        try:
            from gi.repository import GLib
        except ImportError:
            self.skipTest("GLib unavailable")
        self.device("event5")
        trigger = self.trigger()
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        trigger.device.read = lambda: os.read(read_fd, 1) and iter([
            input_event(1, 187, 1)])
        order = []
        trigger.toggle = lambda *args: order.append("M1")
        # Queue the native notification first, then make the evdev source ready.
        # Their callbacks are in the same batch; M1 must claim visibility first.
        GLib.idle_add(lambda: order.append("native") or False, priority=GLib.PRIORITY_DEFAULT)
        GLib.io_add_watch(read_fd, self.watches[0][1], GLib.IO_IN,
                          lambda fd, condition: trigger.on_input(fd, condition) and False)
        os.write(write_fd, b"1")
        loop = GLib.MainContext.default()
        for _ in range(10):
            loop.iteration(False)
            if len(order) == 2:
                break
        self.assertEqual(order, ["M1", "native"])

    def test_reconnect_while_m1_held_does_not_toggle(self):
        self.device("event5")
        trigger = self.trigger()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.disconnect()
            self.active = [187]
            trigger.rescan()
        event = lambda value: input_event(1, 187, value)
        trigger.device.events = [event(1), event(2)]
        trigger.on_input(42, 1)
        self.assertEqual(self.calls, [])
        trigger.device.events = [event(0), event(1)]
        trigger.on_input(42, 1)
        self.assertEqual(len(self.calls), 1)

    def test_dropped_events_resynchronize_without_phantom_toggle(self):
        self.device("event5")
        trigger = self.trigger()
        event = input_event
        self.active = [187]
        trigger.device.events = [event(0, 3, 0), event(1, 187, 1), event(0, 0, 0), event(1, 187, 1)]
        trigger.on_input(42, 1)
        self.assertEqual(self.calls, [])
        trigger.device.events = [event(1, 187, 0), event(1, 187, 1)]
        trigger.on_input(42, 1)
        self.assertEqual(len(self.calls), 1)

    def test_hup_clears_readiness(self):
        self.device("event5")
        trigger = self.trigger()
        device = trigger.device
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(trigger.on_input(42, self.glib.IO_HUP))
        self.assertTrue(device.closed)
        self.assertFalse(backend.read_runtime_status()["ready"])

    def test_stale_process_state_is_not_ready(self):
        backend.runtime_path().write_text(json.dumps({"pid": 999999999, "ready": True}))
        self.assertEqual(backend.read_runtime_status(), {})


if __name__ == "__main__":
    unittest.main()
