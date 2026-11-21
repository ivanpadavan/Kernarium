#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-requested desktop clipboard and serial TTY geometry over virtio-vsock."""

import fcntl
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import subprocess
import termios

PORT = 31337
LIMIT = 2 * 1024 * 1024


def desktop_environment():
    directory = Path('/run/user/1000')
    displays = sorted(p.name for p in directory.glob('wayland-*')
                      if stat.S_ISSOCK(p.stat().st_mode))
    if not displays:
        raise RuntimeError('The Wayland desktop is not ready')
    return ['runuser', '-u', 'debian', '--', 'env',
            f'XDG_RUNTIME_DIR={directory}',
            f'DBUS_SESSION_BUS_ADDRESS=unix:path={directory}/bus',
            f'WAYLAND_DISPLAY={displays[0]}']


def clipboard(arguments, contents=None):
    result = subprocess.run(desktop_environment() + arguments,
                            input=contents, capture_output=True, timeout=5)
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(detail or 'The desktop clipboard is unavailable')
    if len(result.stdout) > LIMIT:
        raise ValueError('Clipboard text exceeds 2 MiB')
    return result.stdout.decode('utf-8', errors='strict')


def dispatch(request):
    if request.get('version') != 1:
        raise ValueError('Unsupported session protocol')
    operation = request.get('operation')
    if operation == 'clipboard-write':
        text = request['text']
        if not isinstance(text, str) or '\0' in text:
            raise ValueError('Clipboard data must be text without NUL characters')
        clipboard(['wl-copy', '--type', 'text/plain;charset=utf-8'], text.encode('utf-8'))
        return {}
    if operation == 'clipboard-read':
        return {'text': clipboard(['wl-paste', '--no-newline', '--type', 'text'])}
    if operation == 'terminal-size':
        rows, columns = request['rows'], request['columns']
        if (type(rows) is not int or type(columns) is not int or
                not 1 <= rows <= 65535 or not 1 <= columns <= 65535):
            raise ValueError('Invalid terminal geometry')
        descriptor = os.open('/dev/ttyS0', os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
        try:
            fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
            actual = fcntl.ioctl(descriptor, termios.TIOCGWINSZ, bytes(8))
            actual_rows, actual_columns, _, _ = struct.unpack('HHHH', actual)
            return {'rows': actual_rows, 'columns': actual_columns}
        finally:
            os.close(descriptor)
    raise ValueError('Unknown session operation')


def receive(connection, length):
    result = bytearray()
    while len(result) < length:
        block = connection.recv(length - len(result))
        if not block:
            raise EOFError('Session request ended early')
        result.extend(block)
    return result


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if self.client_address[0] != socket.VMADDR_CID_HOST:
            return
        self.request.settimeout(10)
        try:
            length, = struct.unpack('!I', receive(self.request, 4))
            if not 0 < length <= LIMIT:
                raise ValueError('Invalid session request length')
            request = json.loads(receive(self.request, length))
            response = dict(version=1, ok=True, **dispatch(request))
        except (OSError, EOFError, ValueError, KeyError, RuntimeError,
                subprocess.SubprocessError) as error:
            response = dict(version=1, ok=False, error=str(error))
        encoded = json.dumps(response, ensure_ascii=False).encode('utf-8')
        if len(encoded) > LIMIT:
            encoded = b'{"version":1,"ok":false,"error":"Clipboard text exceeds 2 MiB"}'
        try:
            self.request.sendall(struct.pack('!I', len(encoded)) + encoded)
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    address_family = socket.AF_VSOCK
    daemon_threads = True
    block_on_close = False

    def server_bind(self):
        # TCPServer's implementation resolves the first address element as
        # an IP hostname; a vsock address contains a numeric context ID.
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()


if __name__ == '__main__':
    with Server((socket.VMADDR_CID_ANY, PORT), Handler) as server:
        server.serve_forever()
