#!/usr/bin/env python3
"""Run ctl against fake services: setting names survive stop and startup waits for DBus."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="handheld-kbd-controls-") as directory:
    root = Path(directory)
    commands = root / "commands"
    commands.mkdir()
    programs = root / ".local/bin"
    programs.mkdir(parents=True)
    config = root / ".config/handheld-kbd/config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"opacity": 0.72}')
    for command in ("systemctl", "systemd-run", "setsid", "pgrep", "sleep", "kwriteconfig6"):
        path = commands / command
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    helper = programs / "handheld-kbd-dbus"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    bus = commands / "busctl"
    bus.write_text('''#!/bin/sh
n=$(cat "$HHK_TEST_COUNT" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$HHK_TEST_COUNT"
[ "$n" -ge 3 ] || exit 1
echo 's ""'
''')
    bus.chmod(0o755)
    count = root / "count"
    env = dict(os.environ, HOME=str(root), PATH=str(commands) + ":" + os.environ["PATH"],
               HK_DETACHED="1", HHK_TEST_COUNT=str(count))
    result = subprocess.run(["bash", str(ROOT / "bin/handheld-kbd-ctl"),
                             "set", "dock_bottom_margin", "40", "int"],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "dock_bottom_margin set to 40." in result.stdout, result.stdout
    assert "opacityinactiverule set" not in result.stdout
    assert json.loads(config.read_text()) == {"opacity": 0.72, "dock_bottom_margin": 40}
    assert int(count.read_text()) == 3, "ctl returned before the DBus service was ready"
    result = subprocess.run(["bash", str(ROOT / "bin/handheld-kbd-ctl"),
                             "set", "dock_bottom_margin", "-1", "int"],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert json.loads(config.read_text())["dock_bottom_margin"] == 40

print("controls: correct setting confirmation, DBus readiness, margin validation passed")
