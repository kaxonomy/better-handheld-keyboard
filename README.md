# Better Handheld Keyboard

> **Heads up:** this is for **Desktop Mode** (KDE Plasma). Game Mode works
> differently under the hood and isn't covered here.

![Better Handheld Keyboard — colour themes, split, 1×–4× sizes, adjustable transparency, layout-aware labels and custom super-key icons](docs/showcase.gif)

If you've ever tried to do something *real* on a SteamOS handheld in Desktop
Mode — open a terminal, use an app with keyboard shortcuts — you've probably hit
the same wall I did.

The built-in on-screen keyboard just couldn't do it. No working `Ctrl`, so no
`Ctrl+C` in the terminal. No reliable `Tab`, `Esc`, or arrow keys. It was opaque
and covered half the screen, and it felt sluggish to bring up. Fine for typing a
Wi-Fi password; useless for actually using the machine.

So I built the keyboard I wanted instead. Here's what it does differently:

- **It types real keystrokes.** Instead of faking input, it injects keys through
  `/dev/uinput` — the same path a real USB keyboard uses. So `Ctrl`, `Alt`,
  `Shift`, `Super`, `F1`–`F12`, `Tab`, `Esc`, and arrows all genuinely work,
  everywhere. `Ctrl+C` in a terminal just works.
- **It uses the button you already press.** I remapped the hardware keyboard
  button so it summons this keyboard instead of the stock one. Press to show,
  press again to hide — no menus, no Steam Input fiddling.
- **It's see-through.** Adjustable transparency, so it's not blinding the screen
  behind it.
- **It's mine to theme, and yours too.** Layout, colours, key sizes, and opacity
  all live in a JSON file. No code to touch.
- **Twenty layouts.** English (US/UK), German, French, Spanish (Spain and Latin
  America), Italian, Portuguese (Portugal and Brazil), Dutch, Polish, Turkish,
  Russian, Ukrainian, Greek, Arabic, Hebrew, Hindi, Thai and Vietnamese. The 🌐 key
  switches between the ones you've configured and re-skins the keys, so `£`, `ñ`, `ç`,
  `й` and `ก` are drawn where they actually are. `handheld-kbd-locales` picks which.
- **AltGr.** Most layouts keep a third of their alphabet behind it — Polish `ą`, French
  `@`, Turkish `î`. Hold it and the keys show what they'll type.
- **Live Shift preview.** Hold Shift and every key shows the capital or symbol it will
  actually type as its main label — no colour change, just the glyph swaps. Toggle it in
  the tray **Settings ▸ Shift preview**.
- **Predictive text.** A row of tappable suggestions above the keys. It learns
  what *you* type, and `handheld-kbd-build-dict` adds corpus frequencies so it's
  useful from the first keypress. Tapping a suggestion types it as real keys, so
  it works in any app. Off with `"prediction": false`.
- **Four sizes.** The size key cycles **1× → 2× → 3× → 4×** (labelled with the size, no
  cryptic arrow) — each step a genuinely larger keyboard with bigger keys, for thumbs on a
  7-inch panel or precision when you're docked. Cycles live, no relogin (also in the tray
  **Settings ▸ Keyboard size**), and it's kept within the panel so even 4× doesn't clip off
  the edges.
- **Colour themes.** Six ready-made palettes — Midnight, Light, High contrast, Nord,
  Solarized and Rose — from the tray **Settings ▸ Colour theme** or
  `handheld-kbd-ctl set color_theme <name>`. The whole keyboard recolours — keys, labels
  and the predictive-text bar — monochrome launcher icons tint to match, and the labels stay
  legible whatever your desktop GTK theme is.
- **Launcher key.** Tap to send the desktop's Super/Meta action (Application Launcher
  on Plasma); hold and release to latch Super for a shortcut. The icon detects
  **Bazzite**, **Arch**, or generic Linux automatically. Override it from tray
  **Settings ▸ Launcher key icon** or `handheld-kbd-ctl super-icon <auto|bazzite|windows|arch|tux>`.
  The Bazzite artwork comes from its [official press kit](https://github.com/ublue-os/bazzite/tree/main/press_kit).
- **Split keyboard.** Turn on `split` (tray **Settings ▸ Split keyboard**, or
  `handheld-kbd-ctl set split true`) and each row's two halves slide out to the
  left and right edges with a clear gap down the middle — thumb-typing while you
  grip the device. Works over any layout; `split_gap` sets the gap width.
- **Cycle transparency from the keyboard.** The ◐ key steps through
  `opacity_steps` instead of making you edit JSON to see what's underneath.
- **Same place on every device.** It docks flush with the bottom edge, full
  width, at a fixed fraction of the panel height — a Deck LCD, a Deck OLED and a
  Legion Go 2 all get the same keyboard in the same spot.
- **Or put it where you want it.** ✥ turns on free movement: a bar appears along
  the top, drag it anywhere, grab either end to resize, then press ✓ to finish —
  it stays exactly where you left it, like any other window. ⤓ resets it to the
  bottom dock.
- **It doesn't need Steam.** `handheld-kbd-toggle` shows and hides it directly, so
  a dead Steam client can't leave you without a keyboard. Bind it to a shortcut.
- **Your trackpads still work.** While Steam thinks its own keyboard is open, the
  sticks and trackpads navigate *that* instead of moving the pointer. So whenever
  Steam's keyboard appears — the button press that summons this one on a Deck, or a
  controller focusing a text field on any device — it's closed rather than hidden,
  whatever trigger mode you're in, and the pointer stays yours.
- **A tray icon.** Tap to show or hide; right-click for restart, reset position, fix the
  trackpad pointer, stop and start — and a **Settings** submenu that toggles prediction,
  glide typing and the rest, and picks the layout, super-key icon,
  suggestion count and more, without touching `config.json`. The settings are grouped
  under **Typing**, **Layout & appearance** and **Summoning & input** headings.
- **Shortcuts for when it goes wrong.** The same actions appear in the application menu —
  clickable, because typing is the one thing you can't do when the keyboard is the
  problem.
- **Swipe typing.** Drag across the letters instead of tapping them. Taps are
  unaffected — a drag only counts once it's unmistakably not one.
- **Optional summon gestures.** Two-finger swipe up from the bottom edge
  (`gesture_summon`), or auto-show whenever a text field takes focus
  (`show_on_focus`, via AT-SPI). Both off by default.
- **Survives sleep and fullscreen.** It re-initialises after resume, and stays
  visible above fullscreen windows instead of disappearing behind them.

## Install

Upgrading? Just re-run the installer — it won't ask for a password, because the
permission it needs is already there from last time.

Double-click **`Install Better Handheld Keyboard.desktop`**, enter your password
once (it needs `/dev/uinput` access — that's how it types real keys), then **log
out and back in**.

Prefer the terminal? `./install.sh`, then log out and back in.

### ROG Ally / Ally X / Bazzite / HHD

On an ASUS ROG Ally (RC71L) or Ally X (RC72LA) running Plasma 6 Desktop Mode, the keyboard detects
the running controller service. HHD does not require an InputPlumber CLI or Steam
Input. M1 toggles this keyboard directly; M2 keeps its HHD behaviour. Re-running
`./install.sh` upgrades the programs and rules while preserving your settings.

HHD's **Keyboard/Overlay** mode opens Steam's keyboard itself, even with Steam
Input disabled. Its public API does not expose a separate M1 keyboard action.
The HHD backend therefore reads F17 press/release events from the identified ASUS
shortcut device without grabbing it or emitting any keys. HHD remains unchanged.
A narrow KWin rule prevents known Steam OSK windows from taking focus or flashing;
the KWin helper identifies and closes them using Steam identity and keyboard
metadata. Mirror toggles are disabled while the direct M1 listener is ready, so
the Steam notification does not repeat the direct toggle. Device discovery repeats
after disconnects. The helper's 700ms duplicate-window guard also covers a
disconnect immediately after M1; mirror fallback then resumes if the device
remains unavailable.
This follows HHD's [Ally driver](https://github.com/hhd-dev/hhd/blob/master/src/hhd/device/rog_ally/base.py)
and [paddle actions](https://github.com/hhd-dev/hhd/blob/master/src/hhd/controller/base.py);
no HHD configuration or system files are changed.

Both Ally models use the same backend. KWin calls use the keyboard's existing
Python DBus support; the `qdbus6` executable is not required.

On Plasma Wayland, the installer also selects **Better Handheld Keyboard** as
the virtual keyboard. Tapping a text field then opens this keyboard through
KWin's native input-method interface. Leave that provider selected in System
Settings → Virtual Keyboard; **None** disables tap activation. Upgrades preserve
a later provider change made in Settings. Uninstall and
`handheld-kbd-recover --stock-only` restore the previous provider.

Tap activation requires the application to support Wayland text input. M1 and
the usual toggle shortcut remain available in applications that do not. Closing
an automatically opened keyboard allows another tap to open it again. A change
of text-field focus does not hide a keyboard opened manually with M1.

The M1 visibility path is:

1. The ASUS listener filters F17 press/release/repeat events using their kernel
   timestamps. A busy GTK loop cannot turn queued contact bounce into two presses.
   An optional F17 hotkey ignores that same device while the direct listener owns it.
   Once that listener opens successfully, its live desktop keymap is checked:
   Linux `KEY_F17` normally becomes **Launch (8)** in Plasma, not F17. A duplicate
   shortcut for that mapped key running `handheld-kbd-toggle` is removed through
   KGlobalAccel. This prevents the direct press and the launched command toggling
   twice. The desktop entry, other key
   combinations and M2 remain unchanged; the migration is logged. It uses KDE's
   [shortcut API](https://github.com/KDE/kglobalaccel/blob/v6.22.0/src/org.kde.KGlobalAccel.xml).
2. The accepted press reaches the shared toggle handler directly. If a native
   tap-show overtook the physical event, M1 takes ownership of that show; a keyboard
   already open before the press still closes normally.
3. HHD may independently open Steam's OSK. KWin suppresses/closes it, and its
   mirror request uses the direct-trigger guard described above.
4. Plasma visibility replies belong to a particular text-input context and query.
   Superseded replies cannot reopen a dismissed keyboard or hide a new context.
   Deferred hides are cancelled when a newer show takes ownership.

On current Plasma 6, the keyboard's KWin rule uses the OSD layer, above the
Application Launcher and fullscreen windows, while keeping focus disabled. It
remains an ordinary window for screen-lock protection. `handheld-kbd-ctl windows`
reports its layer (8 on KWin 6.6) as well as its geometry. This uses KWin's native
[layer rule](https://github.com/KDE/kwin/blob/v6.6.4/src/rulesettings.kcfg).

The nonexclusive listener does not consume the original hardware F17 event. It
never re-injects F17, and does not watch F17 on unrelated keyboards. Avoid binding
physical F17 to another desktop action on this device.

Free movement uses touch/mouse gestures and KWin's logical `frameGeometry` through
the keyboard's session DBus service. Docking is suspended while moving; Done or
hiding the keyboard saves the geometry reported by KWin before unmapping it. The
drag handle overlays the keys so removing it cannot shift the layout. The helper
restores that rectangle on later shows and restarts, retaining fractional logical
coordinates on scaled displays. Docking anchors to the actual frame as Wayland
resize replies arrive, so GTK's minimum height cannot change the bottom margin.
Position rules no longer force a competing rectangle.
The helper uses the [KWin 6 scripting API](https://develop.kde.org/docs/plasma/kwin/api/).

To leave a gap above the usable bottom edge:

```bash
handheld-kbd-ctl set dock_bottom_margin 40 int
```

The default is 0 logical pixels. The setting applies to all docked sizes, respects
display scaling and panels, and leaves custom positions alone. Large margins are
limited to the available space. It is also in the tray's **Bottom margin** menu.
The command restarts the keyboard; no logout is needed.

From this fork's checkout, these are the update and diagnostic commands:

```bash
git pull --ff-only
./install.sh
# Log out and back in after the first install, or restart an existing session:
handheld-kbd-ctl restart
handheld-kbd-ctl status
handheld-kbd-ctl windows
```

Log out and back in to reload an updated Plasma tap provider; `restart` reloads
the keyboard and its supervisor.

If you specifically need to remove a stale official installation first, run
`./uninstall.sh` from this checkout, then `./install.sh`. Uninstall preserves
`~/.config/handheld-kbd/` but removes the uinput rule, so reinstall may ask for a
password. An ordinary upgrade needs only `./install.sh`.

For trigger, movement and Steam window logging:

```bash
handheld-kbd-ctl set debug true bool
tail -f /tmp/handheld-kbd-out.log /tmp/swap-startup.log
# In another terminal, KWin logs:
journalctl --user -f | grep HHKBD
# Turn detailed logging off afterwards:
handheld-kbd-ctl set debug false bool
```

Debug entries include the process ID, timestamp, M1 event timestamp/value,
DBus sender and request source, and the committed visibility. They distinguish
M1, Steam mirror, DBus controls, hotkeys, InputPlumber, and Plasma automatic hides.

`status` / `diagnostics` reports the backend, DMI, session, service readiness,
selected M1 device, placement and live Steam window metadata without root. It
also reports KWin's actual session mode separately from the command's session
(which may be a tty), and the selected Plasma provider and its live state. It
does not log typed keys or text. Unmatched Steam windows omit their captions to
avoid recording chat/browser text.

Acceptance check on the handheld (Steam Input can stay disabled):

1. Run `handheld-kbd-ctl diagnostics`. Confirm `input backend: hhd`,
   `trigger: ally-m1`, an ASUS input device, and the keyboard/KWin services.
2. Press M1 once to show, again to hide; hold it and repeat ten press/release cycles.
   Each press must toggle once. Steam's OSK must not appear or take focus. Check M2
   still opens its usual HHD overlay.
3. Open Application Launcher, select search, press M1 and tap `test` on the
   keyboard. The Launcher must remain open and receive it. Repeat in Konsole,
   a Qt text field (KWrite), a GTK text field, and a browser text field.
4. Tap ✥, drag the centre handle upward by touch, release, and tap ✓. Repeat with
   a mouse and both resize grips. Run `handheld-kbd-ctl windows` and compare its
   keyboard geometry with the saved geometry in `handheld-kbd-ctl status`.
5. Hide/reopen, then run `handheld-kbd-ctl restart` and reopen. The custom rectangle
   must remain. Repeat on a second display with different scaling if available.
   Also drag and close without pressing Done; reopening must retain that position.
6. Run `handheld-kbd-ctl reset`, then set the bottom margin to 40 as above. Check
   all four sizes: `handheld-kbd-ctl windows` should report `bottomMargin: 40`.
7. Suspend/resume, repeat M1 show/hide, and inspect diagnostics for the selected
   event device. No InputPlumber installation is needed.
8. With **Better Handheld Keyboard** selected in Plasma's Virtual Keyboard
   settings, tap a Wayland Qt/GTK/browser text field. Only this keyboard should
   open. Type, close it, and tap the same field again. Repeat after restarting
   the keyboard. Clicking with a mouse should follow Plasma's normal touch-only
   activation policy. Check that M1 still works in applications without native
   text-input support.

These hardware checks require an actual Plasma/Ally session. Unknown future Steam
OSK metadata may require updating the matcher and pre-map rule; use `windows` to
inspect it. Ordinary grabbed application popup menus can dismiss when touched
outside their application; the Application Launcher focus test is separate.

Local regression checks (Node.js is only needed for the KWin mock test):

```bash
python3 tools/test-backend.py
python3 tools/test-movement.py
python3 tools/test-install.py
python3 tools/test-controls.py
python3 tools/test-dbus.py
python3 tools/test-input-method.py
python3 tools/test-keys.py
node tools/test-kwin.js
```

## How it works

```
  keyboard button ──remap──▶ InputPlumber ──DBus event──▶ handheld-kbd
                                                    tap a key │
                                                              ▼
                              focused app ◀── real keystroke ◀── /dev/uinput
```

The button is remapped to fire a **DBus event** rather than a keystroke, so
nothing else reacts to it. Key taps go through **`/dev/uinput`** at the kernel
level — which is why they reach any app.

## No keyboard at all? Read this

If you installed an earlier version on a **Legion Go 1** — or on a Steam Deck / ROG Ally
running Bazzite or ChimeraOS — you may have ended up with *no* on-screen keyboard. Sorry.
The installer picked its "seamless" trigger by looking at InputPlumber's default profile,
which advertises a keyboard button on every device, and remapped a button your hardware
never actually sends. Meanwhile it kept Steam's own keyboard transparent. Both keyboards
gone.

Fix it either way:

```bash
handheld-kbd-recover          # puts Steam's keyboard back, switches this one to mirror mode
./install.sh                  # re-running the installer now repairs the same thing
```

Or double-click **`Recover My Keyboard.desktop`** in this folder — no typing required, which
rather matters when you have no keyboard.

Want out entirely? `handheld-kbd-recover --stock-only` stands everything down and hands the
desktop back to Steam's keyboard.

## Trackpads or stick pointer stopped working?

Versions before 1.0.10 hid Steam's on-screen keyboard instead of closing it. Steam kept
believing its keyboard was open, and while it believes that it puts the controller in its
"KB ActionSet" — sticks and trackpads navigate Steam's keyboard rather than moving the
pointer. 1.0.10 and 1.0.11 closed it, but only in mirror mode, so a device on the DBus or
hotkey trigger (a Legion Go 2, say) could still get stuck; since 1.0.12 it's closed in
every trigger mode. Update, and if a session is still stuck:

```bash
handheld-kbd-fix-pointer
```

Or click **Fix Trackpad and Stick Pointer** in the application menu. It closes the leftover
window; no Steam restart, no relogin.

## Controlling it

The tray icon is the quickest route: tap to show or hide, right-click for the rest. If it
isn't in your system tray, Plasma may be hiding it — check the tray's overflow arrow, or
its settings. `handheld-kbd-tray` starts it by hand.

```bash
handheld-kbd-ctl status      # what's running, and where the keyboard is
handheld-kbd-ctl restart     # the usual fix
handheld-kbd-ctl stop        # until you start it again or log back in
handheld-kbd-ctl reset       # back to the bottom dock
handheld-kbd-ctl set prediction false bool   # set any config key, then restart
handheld-kbd-ctl set layout compact str      # (the tray Settings menu drives this)
```

All of these are in the application menu too.

## Predictive text

Prediction works out of the box from what you type. For suggestions that are useful
before it has learned anything, build the corpus data once:

```bash
handheld-kbd-build-dict
```

That writes `unigrams.txt` and `bigrams.txt` into `~/.local/share/handheld-kbd/`. It
prefers Peter Norvig's `count_1w.txt` / `count_2w.txt` if you drop them in
`~/.local/share/handheld-kbd/raw/`, and falls back to the system aspell dictionary
when offline. Re-runnable, and it never touches `learned.json` — that's your personal
vocabulary, stays on the device, and is in `.gitignore` for a reason.

Turn learning off with `"predict_learn": false` (corpus only), or prediction entirely
with `"prediction": false`.

## Languages

```bash
handheld-kbd-locales                # what's configured, and what labels exist
handheld-kbd-locales set us gb it ru
```

Then log out and back in — KDE reads its layout list at session start — and 🌐 cycles
through them.

Check what this machine can actually manage:

```bash
handheld-kbd-locales --check
```

Shipping labels for twenty languages doesn't make twenty languages work — the layout
comes from `xkeyboard-config` and the glyphs from your system fonts. `--check` reports
both per language, and the installer runs it for you. A missing font means the keys type
correctly but draw as empty boxes; install the Noto font for that script.

**Keep one Latin layout among your four.** Application shortcuts are bound to Latin
keysyms, so with only non-Latin layouts loaded the S key produces (say) `Cyrillic_yeru`
and `Ctrl+S` never reaches Save — likewise `Ctrl+C`, `Ctrl+V`. KDE's Latin fallback
covers global shortcuts, not an application's own. `handheld-kbd-locales` warns if you
pick four without one.

**Four at a time.** Labels ship for twenty layouts, but an XKB keymap holds four groups
at most, so four can be live at once. That is the keymap format, not a setting: ask
libxkbcommon for a fifth and it discards it outright (`Unrecognized RMLVO layout "es" was
ignored`). Swapping which four is a `set` plus a log out.

Worth knowing how this works, because it explains what it can and can't do. The keyboard
injects real keycodes, exactly like a USB keyboard; **what a key types is decided by the
OS layout**, not by this program. So a locale file here contains no behaviour at all, only
the labels to paint on the keys. They're generated from
[xkeyboard-config](https://gitlab.freedesktop.org/xkeyboard-config/xkeyboard-config) — the
same data the OS uses — by `tools/build-locales.py`, rather than typed out by hand in
scripts most of us can't proofread.

Two consequences:

- **A layout with no labels still works.** It types correctly; the keys are just drawn
  with US captions. Any of the hundreds of layouts KDE offers can be added.
- **Chinese, Japanese and Korean aren't here, and can't be.** Those are input methods, not
  keyboard layouts — no mapping of keycodes to characters produces them. Use Fcitx or
  IBus; this keyboard's keystrokes reach it like any other keyboard's.

Vietnamese is a halfway case worth calling out. The `vn` layout puts `ă â ê ô ơ ư đ` on
the number row and tone marks on `5`–`9`, which costs you the digits and can't produce
every syllable. Most Vietnamese typing is done with an IME (Telex or VNI) on a US layout
instead — that works here too, and is probably what you want.

## Configure

Everything's in `~/.config/handheld-kbd/config.json` — `opacity`, `layout`,
`locale`, `color_theme`, `size_level`, `super_icon`, `split`, per-key `theme`
colours, key dimensions, optional `hotkey`. Edits apply next time the keyboard
restarts. Layouts and locales sit beside it as plain JSON.

## Uninstall

`./uninstall.sh` — removes the program, autostart, shortcuts, the KWin script and
the udev rule. Your config in `~/.config/handheld-kbd/` is left in place; delete it
yourself if you want the learned predictions gone too.

## Requirements

KDE Plasma 6 (Wayland) · `python3`, `python-gobject` (GTK 3), `python-evdev`.
The installer adds you to the `input` group.

## Support

If this keyboard made your handheld more usable, you can [buy me a coffee on
Ko-fi](https://ko-fi.com/adamlovatttdevops). It runs entirely on white Monster
and Claude tokens.

## License

MIT — see [LICENSE](LICENSE). It's all local; nothing leaves your device.
