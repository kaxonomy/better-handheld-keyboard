#!/bin/bash
# Better Handheld Keyboard swap daemon (MIRROR, self-correcting). Ours mirrors Steam's OSK
# visibility via /tmp/handheld-kbd.vis. Also loads the opacity KWin script at start.
export DISPLAY="${DISPLAY:-:0}"
KBD="$HOME/.local/bin/handheld-kbd.py"
BACKEND="$HOME/.local/bin/handheld_kbd_backend.py"
detect_input() {
    python3 "$BACKEND" --json | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d["backend"], d["trigger"])'
}
read -r INPUT_BACKEND INPUT_TRIGGER < <(detect_input)
INPUT_BACKEND=${INPUT_BACKEND:-generic}
INPUT_TRIGGER=${INPUT_TRIGGER:-mirror}

# Mirror Steam's OSK by default (hardware keyboard-button trigger). Set "mirror": false
# in config.json to instead drive the keyboard with a controller chord / hotkey — then
# the daemon won't hide what the hotkey just showed.
MIRROR=$(python3 -c 'import json,os
try: print(0 if json.load(open(os.path.expanduser("~/.config/handheld-kbd/config.json"))).get("mirror", True) is False else 1)
except Exception: print(1)' 2>/dev/null)
[ "$MIRROR" = 0 ] || MIRROR=1
CONFIG_MIRROR=$MIRROR
LOCAL_TRIGGER=$(python3 -c 'import json,os
try:
    d=json.load(open(os.path.expanduser("~/.config/handheld-kbd/config.json")))
    print(1 if d.get("hotkey") or d.get("gesture_summon") else 0)
except Exception: print(0)' 2>/dev/null)

exec 9>/tmp/handheld-kbd-swap.lock
flock -n 9 || exit 0

OPSCRIPT="$HOME/.local/share/kwin/scripts/handheld-kbd-opacity/contents/code/main.js"
PROVEN="$HOME/.local/share/handheld-kbd/trigger-proven"

# Regenerate the KWin opacity script from config.json so `opacity` is configurable.
OP=$(python3 -c 'import json,os
try: print(float(json.load(open(os.path.expanduser("~/.config/handheld-kbd/config.json")))["opacity"]))
except Exception: print(0.72)' 2>/dev/null)
case "$OP" in ''|*[!0-9.]*) OP=0.72 ;; esac

# Hiding Steam's OSK is only safe once we know ours can actually come up. In mirror mode
# it can by definition — Steam's OSK is what drives ours. In seamless mode the keyboard
# touches PROVEN the first time it is shown for real, so until that exists we leave
# Steam's keyboard alone: a trigger that never fires must degrade to "the stock keyboard",
# never to "no keyboard at all". (That was the Legion Go 1 case — InputPlumber's default
# profile carries a 'Keyboard' button mapping on every device, but no Go 1 button emits it.)
KEYBOARD_READY=0
refresh_keyboard_ready() {
    KEYBOARD_READY=0
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus NameHasOwner s org.handheld.Keyboard \
        2>/dev/null | grep -q true && KEYBOARD_READY=1
}
hide_steam_wanted() { { [ "$KEYBOARD_READY" = 1 ] && { [ "$MIRROR" = 1 ] || [ "$INPUT_TRIGGER" = ally-m1 ] || [ -f "$PROVEN" ]; }; } && echo 1 || echo 0; }

# XWayland focus happens before windowAdded, so the script alone cannot prevent Steam
# dismissing a launcher. Enable this narrow rule only while our keyboard is available.
STEAM_RULE_UUID="6c4263a8-3263-4d41-85f7-75c704113edb"
STEAM_RULES=( "$STEAM_RULE_UUID" 6c4263a8-3263-4d41-85f7-75c704113edc 6c4263a8-3263-4d41-85f7-75c704113edd )
STEAM_RULE_MODE=""
steam_rule_mode() {
    [ "$STEAM_RULE_MODE" != "$1" ] || return
    command -v kwriteconfig6 >/dev/null 2>&1 || return
    for rule in "${STEAM_RULES[@]}"; do
        for key in acceptfocusrule opacityactiverule opacityinactiverule; do
            kwriteconfig6 --file kwinrulesrc --group "$rule" --key "$key" "$1"
        done
    done
    qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    STEAM_RULE_MODE=$1
}
trap 'steam_rule_mode 0; qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript handheld-kbd-opacity >/dev/null 2>&1 || true' EXIT
trap 'exit 0' TERM INT

update_steam_rule() {
    if [ "$(hide_steam_wanted)" = 1 ]; then
        steam_rule_mode 2
    else
        steam_rule_mode 0
    fi
}

# One writer for the KWin script (shared with the installer and the keyboard's opacity
# key) so the file can never diverge from what those two expect to find in it.
write_opscript() {          # $1 = 1 to also force Steam's OSK transparent
    "$HOME/.local/bin/handheld-kbd-kwin-script" --opacity "$OP" --hide-steam "$1" \
        --mirror "$MIRROR" --out "$OPSCRIPT"
}

load_opscript() {
    qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "handheld-kbd-opacity" >/dev/null 2>&1
    qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$OPSCRIPT" "handheld-kbd-opacity" >/dev/null 2>&1
    qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start >/dev/null 2>&1
}

FALLBACK=/tmp/handheld-kbd.mirror-fallback
set_trigger_mode() {
    rm -f "$FALLBACK"
    MIRROR=$CONFIG_MIRROR
    SEAMLESS_WANTED=0
    if [ "$INPUT_TRIGGER" = ally-m1 ]; then
        # GetTrigger in the live KWin helper skips mirroring while direct M1 is ready.
        # Keep its fallback available if input permissions or a reconnect delay capture.
        MIRROR=1
        : > "$FALLBACK"
    elif [ "$INPUT_BACKEND" = inputplumber ] && [ "$MIRROR" = 0 ]; then
        SEAMLESS_WANTED=1
    elif [ "$INPUT_TRIGGER" = mirror ] && [ "$MIRROR" = 0 ] && [ "$LOCAL_TRIGGER" != 1 ]; then
        # Repair the old unsupported-device seamless mode for this session, preserving
        # the user's config and intentional hotkey/gesture-only setups.
        MIRROR=1
        : > "$FALLBACK"
    fi
}
set_trigger_mode
refresh_keyboard_ready
HIDE_STEAM=$(hide_steam_wanted)
write_opscript "$HIDE_STEAM"

{
  echo "STARTUP $(date) OPSCRIPT=$OPSCRIPT exists=$([ -f "$OPSCRIPT" ] && echo Y || echo N) opacity=$OP mirror=$MIRROR hide_steam=$HIDE_STEAM backend=$INPUT_BACKEND trigger=$INPUT_TRIGGER"
  load_opscript
} >>/tmp/swap-startup.log 2>&1

# Seamless mode: remap the hardware keyboard button (via InputPlumber) so it fires our
# DBus event instead of triggering Steam's OSK. If that remap can't be applied, fall back
# to mirror mode for this session — better a keyboard on Steam's trigger than no keyboard.
# The fallback is recorded in a file, not just a variable: the keyboard regenerates the
# KWin script too (opacity key, placement changes) and would otherwise write MIRROR=0 back,
# silently undoing the fallback while the trigger is still dead.
apply_remap() {          # 0 = the hardware button now drives us
    [ -x "$HOME/.local/bin/handheld-kbd-ip-remap" ] || return 1
    "$HOME/.local/bin/handheld-kbd-ip-remap" >>/tmp/swap-startup.log 2>&1
}

REMAPPED=0
if [ "$SEAMLESS_WANTED" = 1 ]; then
    if apply_remap; then
        REMAPPED=1
    else
        echo "REMAP FAILED $(date) — mirroring Steam's OSK until it can be applied" >>/tmp/swap-startup.log
        : > "$FALLBACK"
        MIRROR=1
        HIDE_STEAM=$(hide_steam_wanted)
        write_opscript "$HIDE_STEAM"
        load_opscript
    fi
fi

# Supervisor loop. This used to run at 10Hz and walk the whole X window tree on every
# tick (xwininfo + grep + an xprop write per match) to notice Steam's on-screen keyboard
# appearing. That is now the KWin script's job — it calls the keyboard over DBus the
# instant that window maps — so this loop only has to keep things alive, and can be slow
# and cheap. On a Steam Deck the old poll was a constant drip of processes competing with
# Steam Input, which drives the trackpads.
while true; do
    # Controller daemons can start after Plasma or be restarted in place. Re-detect
    # occasionally, then reopen the keyboard's subscriptions only when the stack changes.
    backend_check=$((${backend_check:-0} + 1))
    if [ "$backend_check" -ge 5 ]; then
        backend_check=0
        read -r detected detected_trigger < <(detect_input)
        if [ -n "$detected" ] && { [ "$detected" != "$INPUT_BACKEND" ] || [ "$detected_trigger" != "$INPUT_TRIGGER" ]; }; then
            echo "BACKEND CHANGED $(date) $INPUT_BACKEND/$INPUT_TRIGGER -> $detected/$detected_trigger" >>/tmp/swap-startup.log
            INPUT_BACKEND=$detected
            INPUT_TRIGGER=$detected_trigger
            set_trigger_mode
            REMAPPED=0
            retry=4
            pkill -f 'python3 .*handheld-kbd\.py' 2>/dev/null || true
            HIDE_STEAM=$(hide_steam_wanted)
            write_opscript "$HIDE_STEAM"
            load_opscript
        fi
    fi
    refresh_keyboard_ready
    update_steam_rule
    # Self-heal: the KWin script's boot-time load can lose the race with KWin startup.
    if [ "$(qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.isScriptLoaded handheld-kbd-opacity 2>/dev/null)" != "true" ]; then
        HIDE_STEAM=$(hide_steam_wanted)
        write_opscript "$HIDE_STEAM"
        load_opscript
    else
        # Seamless mode: once the keyboard has proven it can appear we may start hiding
        # Steam's OSK. Rewrite + reload only when that flips.
        want=$(hide_steam_wanted)
        if [ "$want" != "$HIDE_STEAM" ]; then
            HIDE_STEAM=$want
            write_opscript "$HIDE_STEAM"
            load_opscript
        fi
    fi

    # InputPlumber drops its composite device whenever the controller re-enumerates — on a
    # Legion Go that happens when the pads are detached, or the service restarts. The remap
    # goes with it and the hardware button quietly stops working. Keep trying until it
    # sticks, then drop the mirror fallback.
    if [ "$SEAMLESS_WANTED" = 1 ] && [ "$REMAPPED" = 0 ]; then
        retry=$((${retry:-0} + 1))
        if [ "$retry" -ge 5 ]; then          # every ~10s
            retry=0
            if apply_remap; then
                REMAPPED=1
                rm -f "$FALLBACK"
                MIRROR=0
                echo "REMAP RECOVERED $(date) — hardware button live again" >>/tmp/swap-startup.log
                HIDE_STEAM=$(hide_steam_wanted)
                write_opscript "$HIDE_STEAM"
                load_opscript
            fi
        fi
    elif [ "$SEAMLESS_WANTED" = 1 ] && [ "$REMAPPED" = 1 ]; then
        # ...and notice if it goes away again.
        check=$((${check:-0} + 1))
        if [ "$check" -ge 15 ]; then         # every ~30s
            check=0
            if ! busctl --system tree org.shadowblip.InputPlumber 2>/dev/null | grep -q CompositeDevice; then
                REMAPPED=0
                echo "REMAP LOST $(date) — InputPlumber has no composite device" >>/tmp/swap-startup.log
            fi
        fi
    fi

    # The tray icon is the way back when the keyboard misbehaves, so it is the last
    # thing that should be missing. Plasma restarts take it with them.
    # Started as its own systemd unit, not as our child: stopping this supervisor stops
    # its whole cgroup, and the tray is the one thing that must survive a keyboard
    # restart — it is where the user clicked "Restart" from.
    if [ -x "$HOME/.local/bin/handheld-kbd-tray" ] \
       && ! pgrep -f 'python3 .*handheld-kbd-tray' >/dev/null; then
        systemctl --user reset-failed handheld-kbd-tray >/dev/null 2>&1
        systemd-run --user --collect --quiet --unit=handheld-kbd-tray \
            python3 "$HOME/.local/bin/handheld-kbd-tray" >/dev/null 2>&1 \
          || setsid python3 "$HOME/.local/bin/handheld-kbd-tray" </dev/null \
               >>/tmp/handheld-kbd-tray.log 2>&1 &
    fi

    # Watchdog: the keyboard's Wayland connection drops when Steam restarts or the
    # compositor churns. Respawn it hidden so the next summon is instant.
    if ! pgrep -f 'python3 .*handheld-kbd\.py' >/dev/null; then
        # keep the keyboard's own output: it is where startup problems show up
        setsid python3 "$KBD" </dev/null >>/tmp/handheld-kbd-out.log 2>&1 &
        sleep 2
    fi

    sleep 2
done
