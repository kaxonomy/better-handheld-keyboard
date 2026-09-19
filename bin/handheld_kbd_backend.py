#!/usr/bin/env python3
"""Runtime controller detection and the HHD Ally Desktop Mode trigger."""
import argparse
import glob
import http.client
import json
import os
from pathlib import Path
import re
import shlex
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


def m1_shortcut_keys():
    """Translate physical M1 through the compositor's current keyboard map."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("M1 desktop keymap unavailable: no GDK display")
    keymap = Gdk.Keymap.get_for_display(display)
    if keymap is None:
        raise RuntimeError("M1 desktop keymap unavailable")
    found, entries, values = keymap.get_entries_for_keycode(KEY_F17 + 8)
    # Linux F17 commonly becomes XF86Launch8 in inet(evdev). KDE shortcuts use
    # the translated Qt key, not evdev 187. Inspect only unmodified levels.
    known = {Gdk.KEY_F17: (0x01000040, "F17"), Gdk.KEY_Launch8: (0x010000aa, "Launch8")}
    keys = {}
    unknown = []
    for entry, value in zip(entries or [], values or []):
        if entry.level != 0:
            continue
        if value in known:
            code, name = known[value]
            keys[code] = name
        else:
            unknown.append(Gdk.keyval_name(value) or hex(value))
    if not found or not keys or unknown:
        raise RuntimeError("M1 desktop keymap unsupported: " + (", ".join(unknown) or "no unmodified F17/Launch8 mapping"))
    return keys


def resolve_m1_shortcut_conflicts(shortcut_keys=None):
    """Retire only duplicate unmodified M1 shortcuts for our toggle command."""
    if shortcut_keys is None:
        shortcut_keys = m1_shortcut_keys()
    from gi.repository import Gio, GLib
    try:
        from gi.repository import GioUnix
    except ImportError:
        GioUnix = Gio                  # DesktopAppInfo lived in Gio on older GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    def call(method, signature, values):
        return bus.call_sync("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel",
                             method, GLib.Variant(signature, values), None,
                             Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()

    # Use the v2 API so other multi-step shortcuts survive; the deprecated
    # integer API loses their later strokes.
    shortcuts = []
    for code in shortcut_keys:
        shortcuts.extend(call("globalShortcutsByKey", "((ai)(i))", (([code, 0, 0, 0],), (0,)))[0])
    expected = os.path.realpath(os.path.expanduser("~/.local/bin/handheld-kbd-toggle"))
    changed = []
    for info in shortcuts:
        action, title, component, friendly, context = info[:5]
        if action != "_launch" or not component.endswith(".desktop"):
            continue
        desktop = GioUnix.DesktopAppInfo.new(component)
        if desktop is None:
            continue
        try:
            argv = shlex.split(desktop.get_commandline() or "")
        except ValueError:
            continue
        if not argv or argv[1:] not in ([], ["toggle"]):
            continue
        executable = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
        if not executable or os.path.realpath(executable) != expected:
            continue
        action_id = [component + ("|" + context if context and context != "default" else ""),
                     action, friendly, title]
        keys = call("shortcutKeys", "(as)", (action_id,))[0]
        retained = [key for key in keys if not (1 <= len(key[0]) <= 4
                    and key[0][0] in shortcut_keys and not any(key[0][1:]))]
        if len(retained) == len(keys):
            continue
        # This is KGlobalAccel's supported foreign-action edit: NoAutoloading,
        # persistent settings, and notification to the owning component.
        call("setForeignShortcutKeys", "(asa(ai))", (action_id, retained))
        actual = call("shortcutKeys", "(as)", (action_id,))[0]
        if {tuple(key[0]) for key in actual} != {tuple(key[0]) for key in retained}:
            raise RuntimeError("Plasma M1 shortcut change did not take effect for " + component)
        changed.append(component)
    return changed


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
            if now < self.last or now - self.last >= 0.08:
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
        self.shortcut_keys = {}
        self.shortcut_checked = False
        self.shortcut_error = ""
        self._status()
        self.rescan()
        self.timer = glib.timeout_add_seconds(2, self.rescan)

    def _log(self, message, debug=False):
        if not debug or self.debug:
            print(f"handheld-kbd: {time.time():.6f} pid={os.getpid()} {message}", file=sys.stderr)

    def _status(self):
        status = {"pid": os.getpid(), "backend": "hhd", "trigger": "ally-m1",
                  "ready": self.device is not None, "device": self.device.path if self.device else "",
                  "event": "KEY_F17 (187)", "error": self.error,
                  "desktop_keys": self.shortcut_keys, "shortcut_error": self.shortcut_error}
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
        self.shortcut_keys = {}
        self.shortcut_checked = False
        self.shortcut_error = ""
        self._status()

    def _check_shortcuts(self):
        if self.shortcut_checked:
            return
        try:
            if not self.shortcut_keys:
                self.shortcut_keys = m1_shortcut_keys()
                self._log("M1 desktop key: " + ", ".join(self.shortcut_keys.values()))
            for component in resolve_m1_shortcut_conflicts(self.shortcut_keys):
                self._log("Removed duplicate Plasma M1 toggle shortcut from " + component
                          + "; direct M1 handles it, other shortcuts unchanged")
            self.shortcut_checked = True
            self.shortcut_error = ""
            self._log("M1 shortcut conflict check complete", debug=True)
        except Exception as ex:
            error = str(ex)
            if error != self.shortcut_error:
                self._log(f"could not check/remove duplicate Plasma M1 toggle shortcuts ({error}); retrying")
            self.shortcut_error = error
        self._status()

    def rescan(self):
        candidates = ally_devices(self.info["dmi"])
        if self.device is not None:
            try:
                stat = os.stat(self.device.path)
                if any(item["path"] == self.device.path for item in candidates) and self.identity == (stat.st_ino, stat.st_rdev):
                    self._check_shortcuts()
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
                self._check_shortcuts()
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
                elif event.type == EV_KEY and event.code == KEY_F17:
                    # evdev's default clock is CLOCK_REALTIME. Callback time is
                    # wrong here: showing the window can block while bounce or
                    # later genuine presses are already queued in the kernel.
                    stamp = event.timestamp()
                    accepted = self.press.feed(event.type, event.code, event.value, stamp)
                    self._log(f"M1 event value={event.value} time={stamp:.6f} "
                              f"{'accepted; toggle requested' if accepted else 'ignored'}", debug=True)
                    if not accepted:
                        continue
                    # This listener runs in the keyboard's own GLib loop. Calling
                    # its Toggle handler now avoids a self-DBus round trip during
                    # which Plasma could show the keyboard automatically first.
                    try:
                        self.toggle(stamp)
                    except Exception as ex:
                        self._log(f"M1 toggle failed ({ex})")
        except BlockingIOError:
            pass
        except OSError as ex:
            self.error = str(ex)
            self.disconnect(from_watch=True)
            return False
        return True


def hhd_trigger_ready(device_path=None):
    return any(trigger.device is not None and (device_path is None or trigger.device.path == device_path)
               for trigger in _keepalive)


def setup_hhd_trigger(info, config, toggle):
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
                 "selected_m1_event": "KEY_F17 (187)" if info["ally_m1"] else "", "error": runtime.get("error", ""),
                 "desktop_keys": runtime.get("desktop_keys", {}), "shortcut_error": runtime.get("shortcut_error", "")})
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
        print(f"M1 desktop key: {', '.join(info['desktop_keys'].values()) or 'unknown'}")
        print(f"M1 listener: {'ready' if info['ready'] else 'inactive'}")
        if info["error"]:
            print(f"M1 detail: {info['error']}")
        if info["shortcut_error"]:
            print(f"M1 shortcut detail: {info['shortcut_error']}")


if __name__ == "__main__":
    main()
