#!/usr/bin/env python3
"""Exercise the real libwayland client ABI with a small socketpair compositor."""
import array
import ctypes
import os
from pathlib import Path
import runpy
import socket
import struct
import threading


try:
    ctypes.CDLL("libwayland-client.so.0")
except OSError:
    print("input-method protocol tests skipped: libwayland-client.so.0 unavailable")
    raise SystemExit(0)

provider = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/handheld-kbd-input-method"))
client, server = socket.socketpair()
server.settimeout(5)
objects = {1: "wl_display"}
globals_ = ("wl_compositor", "wl_shm", "wl_output", "zwp_input_panel_v1", "zwp_input_method_v1")
requests, failures, fds = [], [], []
method_id = None
early_sync = None
context_id = 0xff000000


def uint(*values):
    return struct.pack("=" + "I" * len(values), *values)


def string(value):
    data = value.encode() + b"\0"
    return uint(len(data)) + data + b"\0" * (-len(data) % 4)


def event(obj, opcode, body=b""):
    server.sendall(uint(obj, (len(body) + 8) << 16 | opcode) + body)


def done(callback):
    # A roundtrip returns at done. Queue delete_id in the same write so closing
    # the client immediately afterward cannot race a second server write.
    server.sendall(uint(callback, 12 << 16, 1, 1, 12 << 16 | 1, callback))


def request(obj, opcode, body):
    global method_id, early_sync
    kind = objects[obj]
    requests.append((kind, opcode, body))
    values = struct.unpack("=" + "I" * (len(body) // 4), body)
    if kind == "wl_display":
        if opcode == 1:
            objects[values[0]] = "wl_registry"
            for i, name in enumerate(globals_, 10):
                event(values[0], 0, uint(i) + string(name) + uint(1))
        else:
            # Replay an already-active context during registry discovery, before
            # the provider has finished creating its transparent shm buffer.
            if method_id is None:
                early_sync = values[0]
            else:
                done(values[0])
    elif kind == "wl_registry":
        size = values[1]
        name = body[8:8 + size - 1].decode()
        version, proxy = struct.unpack_from("=II", body, 8 + (size + 3) // 4 * 4)
        assert version == 1 and name == globals_[values[0] - 10]
        objects[proxy] = name
        if name == "wl_shm":
            event(proxy, 0, uint(0))
        elif name == "wl_output":
            event(proxy, 0, uint(0, 0, 1, 1, 0) + string("ASUS") + string("test") + uint(0))
            event(proxy, 1, uint(3, 1920, 1080, 60000))
        elif name == "zwp_input_method_v1":
            method_id = proxy
            objects[context_id] = "zwp_input_method_context_v1"
            event(proxy, 0, uint(context_id))
            # All six protocol events, including surrounding text, are accepted.
            event(context_id, 0, string("private test text") + uint(0, 0))
            event(context_id, 1)
            event(context_id, 2, uint(0, 0))
            event(context_id, 3, uint(0, 0))
            event(context_id, 4, uint(1))
            event(context_id, 5, string("en"))
            done(early_sync)
    elif kind == "wl_compositor":
        objects[values[0]] = "wl_surface" if opcode == 0 else "wl_region"
    elif kind == "wl_shm":
        assert opcode == 0 and values[1] == 4
        fd = fds.pop(0)
        try:
            assert os.pread(fd, 4, 0) == b"\0" * 4
        finally:
            os.close(fd)
        objects[values[0]] = "wl_shm_pool"
    elif kind == "wl_shm_pool" and opcode == 0:
        assert values[1:] == (0, 1, 1, 4, 0)
        objects[values[0]] = "wl_buffer"
    elif kind == "zwp_input_panel_v1":
        assert opcode == 0 and objects[values[1]] == "wl_surface"
        objects[values[0]] = "zwp_input_panel_surface_v1"
    elif kind == "zwp_input_panel_surface_v1":
        assert opcode == 0 and objects[values[0]] == "wl_output"
    elif kind == "wl_surface":
        if opcode == 1:
            assert objects[values[0]] == "wl_buffer" and values[1:] == (0, 0)
        elif opcode == 5:
            assert objects[values[0]] == "wl_region"
        else:
            assert opcode in (0, 6)
    elif kind in ("wl_region", "zwp_input_method_context_v1"):
        assert opcode == 0  # no input region rectangles or key injection


def serve():
    pending = b""
    try:
        while True:
            data, ancillary, _, _ = server.recvmsg(65536, socket.CMSG_SPACE(16))
            if not data:
                return
            for level, kind, payload in ancillary:
                assert level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS
                descriptors = array.array("i")
                descriptors.frombytes(payload)
                fds.extend(descriptors)
            pending += data
            while len(pending) >= 8:
                obj, header = struct.unpack_from("=II", pending)
                size, opcode = header >> 16, header & 0xffff
                if len(pending) < size:
                    break
                request(obj, opcode, pending[8:size])
                pending = pending[size:]
    except Exception as ex:
        failures.append(ex)
        server.close()


thread = threading.Thread(target=serve, daemon=True)
thread.start()
os.environ["WAYLAND_SOCKET"] = str(client.detach())
notifications = []
mapped = []
method = provider["InputMethod"](notifications.append, failures.append, False, lambda: mapped.append(True))
assert method.context and method.surface
assert notifications == [], "only compositor panel visibility may summon the keyboard"
assert method.lib.wl_display_roundtrip(method.display) >= 0
assert any(kind == "wl_surface" and opcode == 6 for kind, opcode, _ in requests)
before = len(method.listeners)
for _ in range(3):
    event(method_id, 1, uint(context_id))
    assert method.lib.wl_display_roundtrip(method.display) >= 0
    assert method.context is None and method.surface is None
    context_id += 1
    objects[context_id] = "zwp_input_method_context_v1"
    event(method_id, 0, uint(context_id))
    assert method.lib.wl_display_roundtrip(method.display) >= 0
    assert method.context and method.surface
    assert method.lib.wl_display_roundtrip(method.display) >= 0
    assert len(method.listeners) == before, "repeated activation leaked listeners"
assert method.lib.wl_display_roundtrip(method.display) >= 0
assert len(mapped) == 4, "each panel commit needs a compositor visibility refresh"
method.close()
thread.join(5)
server.close()
for fd in fds:
    os.close(fd)
assert not failures, failures
assert not thread.is_alive()
print("input-method native protocol, early activation, panel mapping and repeated context tests passed")
