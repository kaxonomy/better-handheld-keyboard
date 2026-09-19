#!/usr/bin/env python3
"""Exercise DBus CLI marshalling with no qdbus executable or desktop session."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import runpy
import select
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/handheld-kbd-dbus"))
xml = """<node>
<interface name="org.kde.kwin.Scripting">
  <method name="loadScript"><arg type="s"/><arg type="s"/><arg type="i" direction="out"/></method>
  <method name="isScriptLoaded"><arg type="s"/><arg type="b" direction="out"/></method>
  <method name="start"/>
</interface>
<interface name="org.kde.KWin"><method name="reconfigure"/></interface>
<interface name="org.freedesktop.DBus.Properties">
  <method name="Set"><arg type="s"/><arg type="s"/><arg type="v"/></method>
</interface>
</node>"""
assert helper["method_info"](xml, "reconfigure") == ("org.kde.KWin", "reconfigure", [])
assert helper["method_info"](xml, "org.kde.kwin.Scripting.loadScript")[2] == ["s", "s"]
overloaded = xml.replace('<method name="start"/>', '<method name="start"/><method name="loadScript"><arg type="s"/></method>')
assert helper["method_info"](overloaded, "org.kde.kwin.Scripting.loadScript", 2)[2] == ["s", "s"]
assert helper["method_info"](overloaded, "org.kde.kwin.Scripting.loadScript", 1)[2] == ["s"]
for name in ("notThere", "other.interface.start"):
    try:
        helper["method_info"](xml, name)
        raise AssertionError("missing method accepted")
    except ValueError:
        pass
ambiguous = xml.replace('</node>', '<interface name="other"><method name="start"/></interface></node>')
try:
    helper["method_info"](ambiguous, "start")
    raise AssertionError("ambiguous method accepted")
except ValueError:
    pass


class Variant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value

    def unpack(self):
        return self.value

    @staticmethod
    def new_tuple(*values):
        return Variant("(" + "".join(v.signature for v in values) + ")", tuple(v.value for v in values))

    @staticmethod
    def parse(signature, value, limit, end):
        assert signature == "v" and value == "<true>"
        return Variant("v", True)


calls = []


def call_sync(service, path, interface, method, params, reply_type, flags, timeout, cancellable):
    assert service == "org.kde.KWin" and path == "/Scripting"
    assert 0 < timeout <= 2000
    if method == "Introspect":
        assert interface == "org.freedesktop.DBus.Introspectable" and params is None
        return Variant("(s)", (xml,))
    calls.append((interface, method, params.signature if params else "()", params.unpack() if params else ()))
    result = {"loadScript": (7,), "isScriptLoaded": (True,), "start": (), "Set": ()}[method]
    return Variant("()", result)


gio = SimpleNamespace(bus_get_sync=lambda *args: SimpleNamespace(call_sync=call_sync),
                      BusType=SimpleNamespace(SESSION=0), DBusCallFlags=SimpleNamespace(NONE=0))
glib = SimpleNamespace(Variant=Variant, VariantType=SimpleNamespace(new=lambda value: value))
with patch.dict(sys.modules, {"gi.repository": SimpleNamespace(Gio=gio, GLib=glib)}):
    for args, expected in ((["loadScript", "/tmp/script with spaces.js", "helper"], "7\n"),
                           (["isScriptLoaded", "helper"], "true\n"), (["start"], ""),
                           (["Set", "org.kde.kwin.VirtualKeyboard", "enabled", "<true>"], "")):
        with redirect_stdout(io.StringIO()) as output:
            assert helper["main"](["org.kde.KWin", "/Scripting"] + args) == 0
        assert output.getvalue() == expected
    assert calls[0] == ("org.kde.kwin.Scripting", "loadScript", "(ss)", ("/tmp/script with spaces.js", "helper"))
    assert calls[-1] == ("org.freedesktop.DBus.Properties", "Set", "(ssv)", ("org.kde.kwin.VirtualKeyboard", "enabled", True))
    with redirect_stderr(io.StringIO()) as error:
        assert helper["main"](["org.kde.KWin", "/Scripting", "loadScript"]) == 1
    assert "expects 2 arguments" in error.getvalue()
    gio.bus_get_sync = lambda *args: (_ for _ in ()).throw(OSError("session bus unavailable"))
    with redirect_stderr(io.StringIO()) as error:
        assert helper["main"](["org.kde.KWin", "/Scripting", "start"]) == 1
    assert "session bus unavailable" in error.getvalue()

with redirect_stdout(io.StringIO()) as output:
    helper["print_values"]((False, 0, "line one\nline two", ["us", "gb"]))
assert output.getvalue() == "false\n0\nline one\nline two\nus\ngb\n"
print("dbus: introspection, typed calls, property variants, CLI output and errors passed without qdbus")


def integration():
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        print("dbus: private-bus integration skipped (Gio unavailable)")
        return
    daemon_path = shutil.which("dbus-daemon")
    if not daemon_path:
        print("dbus: private-bus integration skipped (dbus-daemon unavailable)")
        return
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "session.conf"
        config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen>'
                          '<auth>EXTERNAL</auth><policy context="default"><allow user="*"/>'
                          '<allow receive_sender="*"/><allow send_destination="*"/>'
                          '<allow own="*"/></policy></busconfig>')
        daemon = subprocess.Popen([daemon_path, "--config-file=" + str(config), "--print-address", "--nofork"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        loop, thread = None, None
        previous_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        try:
            assert select.select([daemon.stdout], [], [], 5)[0], "private DBus did not start"
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = daemon.stdout.readline().strip()
            assert os.environ["DBUS_SESSION_BUS_ADDRESS"], daemon.stderr.read()
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "RequestName", GLib.Variant("(su)", ("org.kde.KWin", 0)), None,
                          Gio.DBusCallFlags.NONE, 1000, None)
            received = []

            def method(conn, sender, path, interface, name, params, invocation):
                received.append((name, params.unpack()))
                response = {"loadScript": ("(i)", (7,)), "isScriptLoaded": ("(b)", (True,))}
                invocation.return_value(GLib.Variant(*response[name]) if name in response else None)

            info = Gio.DBusNodeInfo.new_for_xml(xml)
            bus.register_object("/Scripting", info.interfaces[0], method, None, None)
            props = Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.kde.kwin.VirtualKeyboard">'
                                               '<property name="enabled" type="b" access="readwrite"/>'
                                               '<property name="mode" type="i" access="readwrite"/>'
                                               '</interface></node>')
            values = {"enabled": GLib.Variant("b", False), "mode": GLib.Variant("i", 0)}

            def get_property(conn, sender, path, interface, name):
                return values[name]

            def set_property(conn, sender, path, interface, name, value):
                values[name] = value
                return True

            bus.register_object("/VirtualKeyboard", props.interfaces[0], None, get_property, set_property)
            loop = GLib.MainLoop()
            thread = threading.Thread(target=loop.run)
            thread.start()
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "bin/handheld-kbd-dbus"),
                       "org.kde.KWin"]
            cases = [(["/Scripting", "loadScript", "/tmp/script with spaces.js", "keyboard"], "7\n"),
                     (["/Scripting", "isScriptLoaded", "keyboard"], "true\n"), (["/Scripting", "start"], "")]
            for name, value, expected in (("enabled", "<true>", "true\n"), ("mode", "<1>", "1\n")):
                cases.extend([(["/VirtualKeyboard", "org.freedesktop.DBus.Properties.Set",
                                "org.kde.kwin.VirtualKeyboard", name, value], ""),
                              (["/VirtualKeyboard", "org.freedesktop.DBus.Properties.Get",
                                "org.kde.kwin.VirtualKeyboard", name], expected)])
            for args, expected in cases:
                result = subprocess.run(command + args, capture_output=True, text=True, timeout=5)
                assert result.returncode == 0, (args, result.stderr)
                assert result.stdout == expected, (args, result.stdout, expected)
            assert received[0] == ("loadScript", ("/tmp/script with spaces.js", "keyboard"))
            print("dbus: real Gio/private bus calls and Properties.Get/Set passed")
        finally:
            if loop:
                loop.quit()
                thread.join(timeout=3)
            daemon.terminate()
            daemon.wait(timeout=3)
            if previous_address is None:
                os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            else:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = previous_address


integration()
