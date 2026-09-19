#!/usr/bin/env python3
"""Hardware-free checks: python3 tools/test-backend.py."""
import contextlib
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


class BackendTests(unittest.TestCase):
    def test_dmi_requires_asus_and_rc72la(self):
        self.assertTrue(backend.is_ally_x(DMI))
        self.assertTrue(backend.is_ally_x({**DMI, "product_name": "ROG Ally X RC72LA_RC72LA_000123206"}))
        self.assertFalse(backend.is_ally_x({**DMI, "sys_vendor": "Other", "board_vendor": "Other"}))
        self.assertFalse(backend.is_ally_x({**DMI, "product_name": "ROG Ally RC71L", "board_name": "RC71L"}))
        self.assertFalse(backend.is_ally_x({**DMI, "product_name": "RC72LAX", "board_name": "RC72LAX"}))
        self.assertFalse(backend.is_ally_x({}))

    def detect(self, hhd_api=False, hhd_process=False, service=False, ip=False, cli=False, config=None):
        def command(args):
            if args[0] == "systemctl":
                return "hhd@user.service loaded active running" if service else ""
            return "b true" if ip else "b false"
        with patch.object(backend, "hhd_api_active", return_value=hhd_api), \
             patch.object(backend, "hhd_process_active", return_value=hhd_process), \
             patch.object(backend, "_command", side_effect=command), \
             patch.object(backend, "read_dmi", return_value=DMI), \
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
        self.device("event2", product="1abe")
        self.device("event3", keys=(187,))
        expected = self.device("event4")
        self.assertEqual(self.candidates(), [{"path": expected, "name": "Asus Keyboard"}])
        self.assertEqual(self.candidates({}), [])

    def trigger(self):
        candidate_fn = backend.ally_devices
        patch.object(backend, "ally_devices", side_effect=lambda dmi: candidate_fn(dmi, self.sysfs, self.devroot)).start()
        self.calls, self.opened, self.removed = [], [], []
        self.active = []
        owner = self
        class Device:
            def __init__(self, path):
                owner.opened.append(path)
                self.path, self.name, self.fd = path, "Asus Keyboard", 42
                self.info = SimpleNamespace(vendor=backend.ASUS_VENDOR, product=backend.ALLY_X_PRODUCT)
                self.closed, self.events = False, []
            def active_keys(self): return owner.active
            def capabilities(self): return {backend.EV_KEY: [187, 188, 193]}
            def read(self):
                events, self.events = self.events, []
                return iter(events)
            def close(self): self.closed = True
        self.device_class = Device
        self.glib = SimpleNamespace(IO_IN=1, IO_HUP=2, IO_ERR=4,
            io_add_watch=lambda *args: 1, timeout_add_seconds=lambda *args: 2,
            source_remove=self.removed.append)
        bus = SimpleNamespace(call=lambda *args: self.calls.append(args))
        self.gio = SimpleNamespace(BusType=SimpleNamespace(SESSION=1),
            DBusCallFlags=SimpleNamespace(NONE=0), bus_get_sync=lambda *args: bus)
        with contextlib.redirect_stderr(io.StringIO()):
            return backend.AllyM1Trigger({"dmi": DMI}, {}, self.glib, self.gio, Device)

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

    def test_events_toggle_only_m1_and_never_write(self):
        self.device("event5")
        self.device("event6", vendor="1234")
        trigger = self.trigger()
        self.assertEqual(len(self.opened), 1)
        event = lambda kind, code, value: SimpleNamespace(type=kind, code=code, value=value)
        trigger.device.events = [event(1, 188, 1), event(1, 187, 1), event(1, 187, 2), event(1, 187, 1)]
        trigger.on_input(42, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][3], "Toggle")
        # Fake evdev has no write, grab, or uinput methods: passively reading is enough.

    def test_device_identity_rechecked_after_open(self):
        self.device("event5")
        trigger = self.trigger()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.disconnect()
            self.device_class.capabilities = lambda device: {backend.EV_KEY: [187]}
            trigger.rescan()
        self.assertIsNone(trigger.device)
        self.assertFalse(backend.read_runtime_status()["ready"])

    def test_reconnect_while_m1_held_does_not_toggle(self):
        self.device("event5")
        trigger = self.trigger()
        with contextlib.redirect_stderr(io.StringIO()):
            trigger.disconnect()
            self.active = [187]
            trigger.rescan()
        event = lambda value: SimpleNamespace(type=1, code=187, value=value)
        trigger.device.events = [event(1), event(2)]
        trigger.on_input(42, 1)
        self.assertEqual(self.calls, [])
        trigger.device.events = [event(0), event(1)]
        trigger.on_input(42, 1)
        self.assertEqual(len(self.calls), 1)

    def test_dropped_events_resynchronize_without_phantom_toggle(self):
        self.device("event5")
        trigger = self.trigger()
        event = lambda kind, code, value: SimpleNamespace(type=kind, code=code, value=value)
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
