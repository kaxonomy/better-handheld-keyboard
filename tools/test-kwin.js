#!/usr/bin/env node
// Run the generated KWin script against a small compositor mock (no Plasma required).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'handheld-kbd-kwin-'));
fs.mkdirSync(path.join(home, '.config/handheld-kbd'), {recursive: true});
const generator = path.resolve(__dirname, '../bin/handheld-kbd-kwin-script');
function signal() {
    const callbacks = [];
    return {connect(fn) { callbacks.push(fn); }, emit(...args) { callbacks.forEach(fn => fn(...args)); }};
}
let serial = 0;
function window(properties = {}) {
    return Object.assign({internalId: ++serial, resourceClass: '', caption: '',
        frameGeometry: {x: 0, y: 0, width: 400, height: 200},
        frameGeometryChanged: signal(), captionChanged: signal(), desktopFileNameChanged: signal(),
        fullScreenChanged: signal(), minimizedChanged: signal(),
        closeWindow() { this.closed = true; }}, properties);
}
function run(config = {}, args = [], windows = []) {
    fs.writeFileSync(path.join(home, '.config/handheld-kbd/config.json'), JSON.stringify(config));
    const output = path.join(home, 'main.js');
    execFileSync(generator, ['--out', output, ...args], {env: {...process.env, HOME: home}});
    const source = fs.readFileSync(output, 'utf8');
    const calls = [];
    const timers = [];
    const context = {print() {}, KWin: {MaximizeArea: 0}, trigger: '', nextRect: '',
        workspace: {windowList: () => windows, windowAdded: signal(), windowRemoved: signal(),
            clientArea: () => ({x: -1280, y: 24, width: 1280, height: 752})},
        QTimer: function() { this.timeout = signal(); this.start = () => timers.push(this); },
        callDBus(service, object, iface, method, ...args) {
            calls.push({method, args});
            const callback = args[args.length - 1];
            if (typeof callback === 'function') callback(method === 'GetTrigger' ? context.trigger : context.nextRect);
        }};
    vm.createContext(context);
    vm.runInContext(source, context);
    return {context, calls, timers, add(w) { windows.push(w); context.workspace.windowAdded.emit(w); }};
}
try {
    const {context: c} = run({}, ['--diagnostics']);
    for (const caption of ['Steam Input On-screen Keyboard', 'Steam Keyboard', 'SP Keyboard', 'On-screen Keyboard', 'Virtual Keyboard']) {
        assert(c.isSteamOsk(window({resourceClass: 'steamwebhelper', caption})), caption);
    }
    assert(c.isSteamOsk(window({resourceName: 'steam', windowRole: 'osk', caption: 'localized title'})));
    assert(c.isSteamOsk(window({desktopFileName: 'com.valvesoftware.Steam', windowRole: 'keyboard'})));
    assert(c.isSteamOsk(window({resourceClass: 'steam_osk'})));
    for (const w of [window({resourceClass: 'firefox', caption: 'Steam Input On-screen Keyboard'}),
                    window({resourceClass: 'steam', caption: 'Steam Store'}),
                    window({resourceClass: 'steam', caption: 'Keyboard Settings'}),
                    window({resourceClass: 'steam', caption: 'Friends'})]) assert(!c.isSteamOsk(w));
    assert.equal(c.windowMetadata(window({resourceClass: 'steam', caption: 'private chat text'})).caption, '');

    for (let level = 0; level < 4; ++level) {
        const w = window({resourceClass: 'handheld-kbd'});
        run({size_level: level, dock_bottom_margin: 40}, [], [w]);
        assert.equal(w.frameGeometry.y + w.frameGeometry.height, 24 + 752 - 40);
        assert.equal(w.frameGeometry.x, -1280);
        assert.equal(w.frameGeometry.width, 1280);
    }
    const defaultDock = window({resourceClass: 'handheld-kbd'});
    run({}, [], [defaultDock]);
    assert.equal(defaultDock.frameGeometry.y + defaultDock.frameGeometry.height, 776);

    const custom = run({position_mode: 'custom', dock_bottom_margin: 80, geometry: {x: -950, y: 120, w: 700, h: 350}});
    const moved = window({resourceClass: 'handheld-kbd'});
    custom.add(moved);
    assert.deepEqual(moved.frameGeometry, {x: -950, y: 120, width: 700, height: 350});
    moved.frameGeometry.x = -900;
    custom.context.dockKbd(moved);
    assert.equal(moved.frameGeometry.x, -900, 'custom geometry must not be repeatedly enforced');
    const saved = {position_mode: 'custom', dock_bottom_margin: 40,
        geometry: {x: -900, y: 120, w: 700, h: 350}};
    const completed = run(saved, [], [moved]);
    assert.equal(moved.frameGeometry.x, -900, 'finishing a move must leave the current frame alone');
    completed.context.workspace.windowRemoved.emit(moved);
    const reopened = window({resourceClass: 'handheld-kbd'});
    completed.add(reopened);
    assert.deepEqual(reopened.frameGeometry, {x: -900, y: 120, width: 700, height: 350},
        'a new Wayland surface must restore the saved frame, ignoring the dock margin');

    const diagnosticKbd = window({resourceClass: 'handheld-kbd'});
    const diagnosticSteam = window({resourceClass: 'steam', caption: 'SP Keyboard'});
    const diag = run({}, ['--diagnostics'], [diagnosticKbd, diagnosticSteam]);
    assert.deepEqual(diagnosticKbd.frameGeometry, {x: 0, y: 0, width: 400, height: 200});
    assert(!diagnosticSteam.closed);
    assert.equal(JSON.parse(diag.calls[0].args[0]).windows.length, 2);
    assert.equal(JSON.parse(diag.calls[0].args[0]).windows[0].bottomMargin, 576);
    const recover = run({}, ['--close-steam'], [diagnosticKbd, diagnosticSteam]);
    assert(diagnosticSteam.closed);
    assert.deepEqual(diagnosticKbd.frameGeometry, {x: 0, y: 0, width: 400, height: 200});
    assert.equal(recover.calls.length, 0);

    const generic = run({mirror: true});
    const genericSteam = window({resourceClass: 'steam', caption: 'SP Keyboard'});
    generic.add(genericSteam);
    genericSteam.captionChanged.emit();
    assert.equal(generic.calls.filter(call => call.method === 'SteamOsk' && call.args[0] === 'Toggle').length, 1);
    assert(genericSteam.closed && genericSteam.opacity === 0);
    const hhd = run({mirror: true});
    hhd.context.trigger = 'ally-m1';
    const hhdSteam = window({resourceClass: 'steam', caption: 'SP Keyboard'});
    hhd.add(hhdSteam);
    assert(hhdSteam.closed);
    assert(hhd.calls.some(call => call.method === 'SteamOsk'));
    assert(!hhd.calls.some(call => call.method === 'Toggle' || call.method === 'GetTrigger'),
           'service must choose the mirror fallback atomically');
    const late = run({mirror: true});
    const lateSteam = window({resourceClass: 'steam', caption: ''});
    late.add(lateSteam);
    lateSteam.caption = 'SP Keyboard';
    lateSteam.captionChanged.emit();
    assert(lateSteam.closed);
    assert.equal(late.calls.filter(call => call.method === 'SteamOsk' && call.args[0] === 'Toggle').length, 1);

    const kbd = window({resourceClass: 'handheld-kbd'});
    const movement = run({}, ['--dock', '0', '--report', '1'], [kbd]);
    assert(movement.calls.some(call => call.method === 'SetGeometry'));
    assert.equal(movement.timers.length, 1);
    movement.context.nextRect = JSON.stringify({x: -860.5, y: 130, w: 800, h: 400});
    movement.timers[0].timeout.emit();
    assert.equal(kbd.frameGeometry.x, -860.5);
    assert.equal(kbd.frameGeometry.y, 130);
    assert(movement.calls.some(call => call.method === 'SetGeometry' && call.args[0] === '-860.5,130,800,400'));
    movement.context.nextRect = JSON.stringify({x: 1, y: 1, w: -1, h: 400});
    movement.timers[0].timeout.emit();
    assert.equal(kbd.frameGeometry.x, -860.5);
    assert.equal(run().timers.length, 0, 'locked mode must not poll');
    assert(!('activeWindow' in movement.context.workspace), 'helper must never activate a window');

    const foreground = window({resourceClass: 'firefox', fullScreen: true, layer: 5, keepBelow: false});
    const launcher = window({resourceClass: 'plasmashell', layer: 6, keepAbove: true});
    const osdKbd = window({resourceClass: 'handheld-kbd', layer: 8, keepAbove: true});
    const stacking = run({}, [], [foreground, launcher, osdKbd]);
    assert.equal(foreground.keepBelow, false, 'native OSD layer needs no fullscreen demotion');
    assert.equal(launcher.layer, 6, 'the launcher layer must remain unchanged');
    assert(!('activeWindow' in stacking.context.workspace), 'stacking must not take keyboard focus');
    assert.equal(stacking.context.windowMetadata(osdKbd).layer, '8');
    assert.equal(stacking.context.windowMetadata(osdKbd).keepAbove, 'true');

    const oldKbd = window({resourceClass: 'handheld-kbd', layer: 3});
    const oldStacking = run({}, [], [foreground, oldKbd]);
    assert.equal(foreground.keepBelow, true, 'old rules must retain the fullscreen fallback');
    oldKbd.layer = 8;
    oldStacking.context.applyStack();
    assert.equal(foreground.keepBelow, false, 'native elevation must restore previous fullscreen demotion');
    console.log('KWin tests passed: OSK matching, mirror/HHD suppression, diagnostics, docking, custom placement, movement, stacking.');
} finally {
    fs.rmSync(home, {recursive: true, force: true});
}
