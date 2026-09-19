#!/usr/bin/env python3
"""Runtime controller detection and the HHD Ally Desktop Mode trigger."""
import argparse
import glob
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

# HHD's Ally driver uses this ASUS shortcut interface, separately from its gamepad.
# https://github.com/hhd-dev/hhd/blob/master/src/hhd/device/rog_ally/base.py
ASUS_VENDOR = 0x0b05
ALLY_PRODUCT = 0x1abe
ALLY_X_PRODUCT = 0x1b4c
ALLY_PRODUCTS = (ALLY_PRODUCT, ALLY_X_PRODUCT)
EV_KEY, EV_SYN, SYN_REPORT, SYN_DROPPED = 1, 0, 0, 3
KEY_F17, KEY_F23 = 187, 193
_keepalive = []


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ""


def read_dmi(root="/sys/class/dmi/id"):
    return {key: _read(Path(root) / key) for key in
            ("sys_vendor", "product_name", "board_vendor", "board_name")}


def is_ally(dmi):
    vendor = (dmi.get("sys_vendor", "") + " " + dmi.get("board_vendor", "")).lower()
    model = (dmi.get("product_name", "") + " " + dmi.get("board_name", "")).upper()
    return "asus" in vendor and bool(re.search(r"(?:^|[^A-Z0-9])(?:RC71L|RC72LA)(?:$|[^A-Z0-9])", model))


def _command(args):
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hhd_api_active(path="/run/hhd/api"):
    """A responding HHD API, not the presence of an old socket or executable."""
    connection = http.client.HTTPConnection("localhost", timeout=0.3)
    try:
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(0.3)
        connection.sock.connect(path)
        connection.request("GET", "/api/v1/version")
        response = connection.getresponse()
        body = response.read(4096)
        return response.status == 200 and isinstance(json.loads(body).get("version"), int)
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def hhd_process_active(proc="/proc"):
    for entry in glob.glob(str(Path(proc) / "[0-9]*" / "cmdline")):
        try:
            args = Path(entry).read_bytes().split(b"\0")
            args = [arg.decode(errors="replace") for arg in args if arg]
        except OSError:
            continue
        if not args:
            continue
        executable = os.path.basename(args[0])
        if executable in ("hhd", "hhd_local"):
            return True
        if executable.startswith("python"):
            for index, arg in enumerate(args[1:], 1):
                if arg == "-c":
                    break
                if arg == "-m":
                    if args[index + 1:index + 2] == ["hhd"]:
                        return True
                    break
                if not arg.startswith("-"):
                    if os.path.basename(arg) in ("hhd", "hhd_local"):
                        return True
                    break
    return False


def choose_backend(hhd_active, inputplumber_active):
    # An active HHD owns this handheld even when InputPlumber is also installed.
    if hhd_active:
        return "hhd"
    return "inputplumber" if inputplumber_active else "generic"


def detect_backend(config=None):
    hhd_source = ""
    if hhd_api_active():
        hhd_source = "API"
    elif _command(["systemctl", "list-units", "--no-legend", "--plain", "--state=active",
                   "hhd.service", "hhd@*.service", "hhd_local@*.service"]):
        hhd_source = "service"
    elif hhd_process_active():
        hhd_source = "process"
    ip_active = _command(["busctl", "--system", "call", "org.freedesktop.DBus",
                          "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                          "s", "org.shadowblip.InputPlumber"]) == "b true"
    dmi = read_dmi()
    backend = choose_backend(bool(hhd_source), ip_active)
    ally_m1 = backend == "hhd" and is_ally(dmi)
    override = (config or {}).get("input_backend", "auto")
    trigger = "ally-m1" if ally_m1 else ("inputplumber" if backend == "inputplumber" else "mirror")
    if override == "generic":
        trigger = "mirror"
    return {"backend": backend, "device": dmi.get("product_name") or dmi.get("board_name") or "unknown",
            "dmi": dmi, "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            "hhd_active": bool(hhd_source), "hhd_status": "active (" + hhd_source + ")" if hhd_source else "inactive",
            "inputplumber_active": ip_active, "inputplumber_status": "active (DBus)" if ip_active else "inactive",
            "inputplumber_cli": bool(shutil.which("inputplumber")), "ally_m1": ally_m1, "trigger": trigger}


def _has_key(bitmap, code):
    # sysfs prints native unsigned-long words, most significant word first.
    import struct
    words = bitmap.split()
    bits = struct.calcsize("L") * 8
    word = code // bits
    return len(words) > word and bool(int(words[-1 - word], 16) & (1 << (code % bits)))


def ally_devices(dmi, sysfs="/sys/class/input", devroot="/dev/input"):
    """Filter sysfs before opening anything; unrelated keyboards are never read."""
    if not is_ally(dmi):
        return []
    devices = []
    for entry in sorted(Path(sysfs).glob("event*")):
        device = entry / "device"
        try:
            vendor = int(_read(device / "id/vendor"), 16)
            product = int(_read(device / "id/product"), 16)
            keys = _read(device / "capabilities/key")
            if (vendor == ASUS_VENDOR and product in ALLY_PRODUCTS and
                    _has_key(keys, KEY_F17) and _has_key(keys, KEY_F23)):
                devices.append({"path": str(Path(devroot) / entry.name), "name": _read(device / "name")})
        except ValueError:
            continue
    return devices


class M1Press:
    """One toggle per press; ignore autorepeat and contact bounce."""
    def __init__(self):
        self.down = False
        self.last = -float("inf")

    def feed(self, kind, code, value, now):
        if kind != EV_KEY or code != KEY_F17:
            return False
        if value == 0:
            self.down = False
        elif value == 1 and not self.down:
            self.down = True
            if now - self.last >= 0.08:
                self.last = now
                return True
        return False


def runtime_path():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"handheld-kbd-backend-{os.getuid()}.json"


def read_runtime_status():
    try:
        status = json.loads(runtime_path().read_text())
        os.kill(int(status["pid"]), 0)
        return status
    except (OSError, ValueError, KeyError, TypeError):
        return {}


class AllyM1Trigger:
    def __init__(self, info, config, glib, input_device, toggle):
        self.info, self.glib, self.input_device = info, glib, input_device
        self.toggle = toggle
        self.debug = bool(config.get("debug", False)) or os.environ.get("HANDHELD_KBD_DEBUG") == "1"
        self.device = None
        self.watch = None
        self.identity = None
        self.press = M1Press()
        self.dropped = False
        self.error = ""
        self._status()
        self.rescan()
        self.timer = glib.timeout_add_seconds(2, self.rescan)

    def _log(self, message, debug=False):
        if not debug or self.debug:
            print("handheld-kbd: " + message, file=sys.stderr)

    def _status(self):
        status = {"pid": os.getpid(), "backend": "hhd", "trigger": "ally-m1",
                  "ready": self.device is not None, "device": self.device.path if self.device else "",
                  "event": "KEY_F17 (187)", "error": self.error}
        try:
            path = runtime_path()
            fd, name = tempfile.mkstemp(prefix=".handheld-kbd-backend-", dir=path.parent)
            with os.fdopen(fd, "w") as target:
                json.dump(status, target)
            os.replace(name, path)
        except OSError as ex:
            self._log(f"cannot write trigger status ({ex})")

    def disconnect(self, from_watch=False):
        if self.watch is not None and not from_watch:
            self.glib.source_remove(self.watch)
        self.watch = None
        if self.device is not None:
            self._log(f"M1 device disconnected: {self.device.path}")
            self.device.close()
        self.device = None
        self.identity = None
        self.press = M1Press()
        self.dropped = False
        self._status()

    def rescan(self):
        candidates = ally_devices(self.info["dmi"])
        if self.device is not None:
            try:
                stat = os.stat(self.device.path)
                if any(item["path"] == self.device.path for item in candidates) and self.identity == (stat.st_ino, stat.st_rdev):
                    return True
            except OSError:
                pass
            self.disconnect()
        for candidate in candidates:
            device = None
            try:
                device = self.input_device(candidate["path"])
                keys = device.capabilities().get(EV_KEY, [])
                # Recheck after open in case an event number was reused during discovery.
                if (device.info.vendor != ASUS_VENDOR or device.info.product not in ALLY_PRODUCTS or
                        KEY_F17 not in keys or KEY_F23 not in keys):
                    device.close()
                    continue
                stat = os.stat(device.path)
                self.identity = (stat.st_ino, stat.st_rdev)
                self.device = device
                self.press = M1Press()
                self.press.down = KEY_F17 in device.active_keys()
                # Resolve the physical press before queued native-panel/Steam
                # notifications from the same press can change visibility.
                self.watch = self.glib.io_add_watch(device.fd, self.glib.PRIORITY_HIGH,
                    self.glib.IO_IN | self.glib.IO_HUP | self.glib.IO_ERR, self.on_input)
                self.error = ""
                self._status()
                self._log(f"M1 listening on {device.path} ({device.name}), KEY_F17; HHD M2 unchanged")
                return True
            except OSError as ex:
                if device is not None:
                    device.close()
                self.device = None
                error = str(ex)
                if self.error != error:
                    self.error = error
                    self._log(f"M1 device unavailable ({error}); retrying")
                    self._status()
        if not candidates and self.error != "ASUS M1 device not found; waiting for reconnect":
            self.error = "ASUS M1 device not found; waiting for reconnect"
            self._log(self.error)
            self._status()
        return True

    def on_input(self, fd, condition):
        try:
            if condition & (self.glib.IO_HUP | self.glib.IO_ERR):
                raise OSError("device disconnected")
            for event in self.device.read():
                if event.type == EV_SYN and event.code == SYN_DROPPED:
                    self.dropped = True
                elif self.dropped:
                    if event.type == EV_SYN and event.code == SYN_REPORT:
                        self.press.down = KEY_F17 in self.device.active_keys()
                        self.dropped = False
                elif self.press.feed(event.type, event.code, event.value, time.monotonic()):
                    self._log("M1 event received; toggle requested", debug=True)
                    # This listener runs in the keyboard's own GLib loop. Calling
                    # its Toggle handler now avoids a self-DBus round trip during
                    # which Plasma could show the keyboard automatically first.
                    try:
                        self.toggle()
                    except Exception as ex:
                        self._log(f"M1 toggle failed ({ex})")
        except BlockingIOError:
            pass
        except OSError as ex:
            self.error = str(ex)
            self.disconnect(from_watch=True)
            return False
        return True


def hhd_trigger_ready():
    return any(trigger.device is not None for trigger in _keepalive)


def setup_hhd_trigger(config, toggle):
    info = detect_backend(config)
    if info["trigger"] != "ally-m1":
        return None
    print(f"handheld-kbd: input backend: {info['backend']}; device: {info['device']}; trigger: ally-m1", file=sys.stderr)
    try:
        from gi.repository import GLib
        from evdev import InputDevice
        trigger = AllyM1Trigger(info, config, GLib, InputDevice, toggle)
        _keepalive.append(trigger)
        return trigger
    except Exception as ex:
        print(f"handheld-kbd: HHD M1 trigger unavailable ({ex})", file=sys.stderr)
        return None


def diagnostics():
    config_path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "handheld-kbd/config.json"
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError):
        config = {}
    info = detect_backend(config)
    runtime = read_runtime_status()
    if info["backend"] == "inputplumber" and (config.get("mirror", True) or not config.get("dbus_trigger")):
        info["trigger"] = "mirror"
    if info["trigger"] != "ally-m1":
        runtime = {}
    candidates = ally_devices(info["dmi"]) if info["ally_m1"] else []
    selected = runtime.get("device") or (candidates[0]["path"] if candidates else "")
    info.update({"ready": bool(runtime.get("ready")) and info["trigger"] == "ally-m1",
                 "m1_devices": candidates, "selected_m1_device": selected,
                 "selected_m1_name": next((item["name"] for item in candidates if item["path"] == selected), ""),
                 "selected_m1_event": "KEY_F17 (187)" if info["ally_m1"] else "", "error": runtime.get("error", "")})
    # A TTY or SSH caller may not share the graphical process's runtime directory.
    # Prefer the running service over that caller's cached status file.
    live = _command(["busctl", "--user", "call", "org.handheld.Keyboard", "/org/handheld/Keyboard",
                     "org.handheld.Keyboard", "GetTrigger"])
    if live.startswith('s "'):
        info["ready"] = live == 's "ally-m1"'
        if info["ready"]:
            info["error"] = ""
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--field")
    args = parser.parse_args()
    if args.field == "ready":
        print(json.dumps(bool(read_runtime_status().get("ready"))))
        return
    info = diagnostics()
    if args.json:
        print(json.dumps(info))
    elif args.field:
        value = info.get(args.field, "")
        print(json.dumps(value) if isinstance(value, (bool, dict, list)) else value)
    else:
        print(f"input backend: {info['backend']}")
        print(f"device: {info['device']}")
        print(f"DMI product: {info['dmi']['product_name'] or 'unknown'}")
        print(f"DMI board: {info['dmi']['board_name'] or 'unknown'}")
        print(f"session type: {info['session_type']}")
        print(f"HHD: {info['hhd_status']}")
        print(f"InputPlumber: {info['inputplumber_status']}; CLI: {'available' if info['inputplumber_cli'] else 'absent'}")
        print(f"trigger: {info['trigger']}")
        print(f"M1 device: {info['selected_m1_device'] or 'none'}")
        if info["selected_m1_name"]:
            print(f"M1 device name: {info['selected_m1_name']}")
        print(f"M1 event: {info['selected_m1_event'] or 'none'}")
        print(f"M1 listener: {'ready' if info['ready'] else 'inactive'}")
        if info["error"]:
            print(f"M1 detail: {info['error']}")


if __name__ == "__main__":
    main()
