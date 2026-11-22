#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phosh's display extension to the host-only guest session protocol."""

import importlib.machinery
import importlib.util
import re
import subprocess
import threading

DISPLAY_LOCK = threading.Lock()


def outputs(text):
    result = []
    output = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            output = {'name': line.split()[0], 'enabled': False, 'modes': []}
            result.append(output)
        elif output is not None:
            if line.strip() == 'Enabled: yes':
                output['enabled'] = True
            # wlr-randr omits the comma and Hz when a custom mode reports
            # refresh=0; its current mode is still a valid pixel geometry.
            mode = re.match(r'\s+(\d+)x(\d+)\s+px(?:,|\s|$)', line)
            if mode:
                size = tuple(map(int, mode.groups()))
                output['modes'].append(size)
                if re.search(r'\bcurrent\b', line):
                    output['current'] = size
    return result


def configure_display(request, command):
    if request.get('version') != 1:
        raise ValueError('Unsupported session protocol')
    width, height = request['width'], request['height']
    if type(width) is not int or type(height) is not int or not (1 <= width <= 4095 and 1 <= height <= 4095):
        raise ValueError('Invalid display geometry')
    if 'metadata' in request and request['metadata'] is not None and not isinstance(request['metadata'], dict):
        raise ValueError('Display metadata must be an object')
    # Metadata is advisory. In particular, host scale is never --scale.
    with DISPLAY_LOCK:
        active = [output for output in outputs(command([])) if output['enabled']]
        if len(active) != 1:
            raise RuntimeError('Expected one active virtual display')
        output = active[0]
        if output.get('current') != (width, height):
            mode_option = '--mode' if (width, height) in output['modes'] else '--custom-mode'
            command(['--output', output['name'], mode_option, f'{width}x{height}'])
        actual = next((o for o in outputs(command([])) if o['name'] == output['name']), {})
        if not actual.get('enabled') or actual.get('current') != (width, height):
            raise RuntimeError(f"The compositor did not apply {width}x{height}; current mode: {actual.get('current')}")
        return {'width': width, 'height': height, 'output': output['name']}


def main():
    # Reuse the base image's framing, peer-CID checks and existing operations.
    loader = importlib.machinery.SourceFileLoader('kernarium_session', '/usr/libexec/kernarium-session')
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    base_dispatch = module.dispatch

    def command(arguments):
        result = subprocess.run(module.desktop_environment() + ['env', 'LC_ALL=C', 'wlr-randr', *arguments],
                                text=True, capture_output=True, timeout=2)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Display configuration failed')
        return result.stdout

    def dispatch(request):
        if request.get('operation') == 'display-scale':
            return configure_display(request, command)
        return base_dispatch(request)

    module.dispatch = dispatch
    with module.Server((module.socket.VMADDR_CID_ANY, module.PORT), module.Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
