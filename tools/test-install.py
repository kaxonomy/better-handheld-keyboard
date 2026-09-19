#!/usr/bin/env python3
"""Exercise install/upgrade/uninstall and supervisor policy without root or KDE."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RULE = "a8a95de3-82aa-4998-87c0-125fb8525143"
STEAM_RULES = ["6c4263a8-3263-4d41-85f7-75c704113ed" + suffix for suffix in "bcd"]


def write_program(path, text):
    path.write_text(text)
    path.chmod(0o755)


with tempfile.TemporaryDirectory(prefix="handheld-kbd-install-test-") as temporary:
    base = Path(temporary)
    home, mocks = base / "home", base / "commands"
    home.mkdir()
    mocks.mkdir()
    state = base / "rules.json"
    state.write_text("{}")
    env = dict(os.environ, HOME=str(home), USER="handheld-test", PATH=str(mocks) + ":" + os.environ["PATH"],
               HHK_TEST_STATE=str(state), HHK_TEST_OWNER="0", PYTHONDONTWRITEBYTECODE="1")
    # The existing injection permission step is intercepted. No host service, process,
    # input device, root file, dictionary download or desktop is touched by this test.
    for name in ("pkexec", "pkill", "systemctl", "qdbus6", "update-desktop-database", "setsid"):
        write_program(mocks / name, "#!/bin/sh\nexit 0\n")
    write_program(mocks / "busctl", '#!/bin/sh\n[ "$HHK_TEST_OWNER" = 1 ] && echo "b true" || echo "b false"\n')
    write_program(mocks / "python3", "#!/bin/bash\n" + r'''
case "$1" in
  */handheld_kbd_backend.py)
    case "$3" in backend) echo hhd ;; trigger) echo ally-m1 ;; esac
    exit 0 ;;
  */handheld-kbd-install-filter) echo installed; exit 0 ;;
  */handheld-kbd-locales) exit 0 ;;
esac
exec ''' + shlex.quote(sys.executable) + ' "$@"\n')
    kde_mock = "#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
path = Path(os.environ["HHK_TEST_STATE"])
data = json.loads(path.read_text())
def value(option, fallback=""):
    return args[args.index(option) + 1] if option in args else fallback
group = value("--group")
key = value("--key")
if Path(sys.argv[0]).name == "kreadconfig6":
    print(data.get(group, {}).get(key, value("--default")))
else:
    if "--delete-group" in args:
        data.pop(group, None)
    elif "--delete" in args:
        data.setdefault(group, {}).pop(key, None)
    else:
        data.setdefault(group, {})[key] = args[-1]
    path.write_text(json.dumps(data))
'''
    write_program(mocks / "kwriteconfig6", kde_mock)
    write_program(mocks / "kreadconfig6", kde_mock)
    dictionary = home / ".local/share/handheld-kbd/unigrams.txt"
    dictionary.parent.mkdir(parents=True)
    dictionary.write_text("test 1\n")

    def run(script):
        result = subprocess.run(["bash", str(ROOT / script)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        assert result.returncode == 0, result.stdout
        return result.stdout

    output = run("install.sh")
    assert "InputPlumber is not required" in output, output
    config_path = home / ".config/handheld-kbd/config.json"
    config = json.loads(config_path.read_text())
    assert config["dock_bottom_margin"] == 0
    assert (home / ".local/bin/handheld_kbd_backend.py").read_bytes() == (ROOT / "bin/handheld_kbd_backend.py").read_bytes()
    rules = json.loads(state.read_text())
    assert rules[RULE]["positionrule"] == rules[RULE]["sizerule"] == "0"
    assert "position" not in rules[RULE] and "size" not in rules[RULE]
    for rule in STEAM_RULES:
        assert rules[rule]["acceptfocusrule"] == rules[rule]["opacityactiverule"] == "0"

    # Customized upgrades keep every setting and a running supervisor's rule state.
    config.update(mirror=False, dock_bottom_margin=40, position_mode="custom",
                  geometry={"x": -100, "y": 75, "w": 850, "h": 320}, custom_setting="keep")
    config_path.write_text(json.dumps(config))
    for rule in STEAM_RULES:
        for key in ("acceptfocusrule", "opacityactiverule", "opacityinactiverule"):
            rules[rule][key] = "2"
    state.write_text(json.dumps(rules))
    env["HHK_TEST_OWNER"] = "1"
    for _ in range(2):
        run("install.sh")
        assert json.loads(config_path.read_text()) == config
        rules = json.loads(state.read_text())
        for rule in STEAM_RULES:
            assert rules[rule]["acceptfocusrule"] == rules[rule]["opacityactiverule"] == "2"
        assert len(rules["General"]["rules"].split(",")) == 4

    # Exercise the actual shell policy, isolated from its infinite supervisor loop.
    source = (ROOT / "bin/handheld-kbd-swap.sh").read_text()
    def function(name):
        start = source.index(name + "() {")
        return source[start:source.index("\n}", start) + 2]
    policy = function("set_trigger_mode") + "\n" + r'''
FALLBACK="$1/fallback"
CONFIG_MIRROR=0; LOCAL_TRIGGER=0
INPUT_BACKEND=hhd; INPUT_TRIGGER=ally-m1
set_trigger_mode
test "$SEAMLESS_WANTED:$MIRROR" = 0:1 && test -f "$FALLBACK" || exit 1
INPUT_BACKEND=inputplumber; INPUT_TRIGGER=inputplumber
set_trigger_mode
test "$SEAMLESS_WANTED:$MIRROR" = 1:0 && test ! -f "$FALLBACK" || exit 2
INPUT_BACKEND=generic; INPUT_TRIGGER=mirror
set_trigger_mode
test "$SEAMLESS_WANTED:$MIRROR" = 0:1 && test -f "$FALLBACK" || exit 3
LOCAL_TRIGGER=1
set_trigger_mode
test "$SEAMLESS_WANTED:$MIRROR" = 0:0 && test ! -f "$FALLBACK" || exit 4
'''
    subprocess.run(["bash", "-c", policy, "policy", str(base)], env=env, check=True)

    suppression = "\n".join((
        function("steam_rule_mode"), function("update_steam_rule"),
        next(line for line in source.splitlines() if line.startswith("hide_steam_wanted()")),
        "STEAM_RULES=( " + " ".join(STEAM_RULES) + " )",
        'STEAM_RULE_MODE=""; KEYBOARD_READY="$1"; MIRROR=1; INPUT_TRIGGER=ally-m1',
        'PROVEN="$2/proven"; update_steam_rule; hide_steam_wanted'))
    for ready in ("1", "0"):
        result = subprocess.run(["bash", "-c", suppression, "suppression", ready, str(base)],
                                env=env, text=True, stdout=subprocess.PIPE, check=True)
        assert result.stdout.strip() == ready
        rules = json.loads(state.read_text())
        for rule in STEAM_RULES:
            for key in ("acceptfocusrule", "opacityactiverule", "opacityinactiverule"):
                assert rules[rule][key] == ("2" if ready == "1" else "0")

    # An unusable InputPlumber profile must leave the supervisor in mirror fallback.
    default_profile = base / "default.yaml"
    remap = (ROOT / "bin/handheld-kbd-ip-remap").read_text().replace(
        "/usr/share/inputplumber/profiles/default.yaml", str(default_profile)).replace(
        "/tmp/handheld-kbd-ip.yaml", str(base / "remapped.yaml"))
    for contents in (None, "name: Unsupported profile format\n"):
        if contents is not None:
            default_profile.write_text(contents)
        result = subprocess.run(["bash", "-c", remap], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        assert result.returncode != 0, "unusable InputPlumber profile reported success"

    run("uninstall.sh")
    assert json.loads(config_path.read_text()) == config
    assert not (home / ".local/bin/handheld_kbd_backend.py").exists()
    rules = json.loads(state.read_text())
    assert not any(rule in rules for rule in [RULE] + STEAM_RULES)
    assert not rules["General"]["rules"]

print("install/upgrade/uninstall: preserved config, HHD without InputPlumber, KWin rules and trigger fallback passed")
