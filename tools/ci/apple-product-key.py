#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fingerprint one Apple dependency product and its selected build environment."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess


def output(*command):
    return subprocess.check_output(command, stderr=subprocess.STDOUT, text=True, timeout=30).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', choices=['gpu', 'network', 'storage', 'compression'], required=True)
    parser.add_argument('--platform', choices=['ios', 'ios-simulator'], required=True)
    arguments = parser.parse_args()
    spec = importlib.util.spec_from_file_location('debian_inputs', Path(__file__).with_name('debian-inputs.py'))
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    record = dict(source=inputs.Repository().dependency(arguments.product, arguments.platform),
                  architecture=platform.machine(), host=output('sw_vers', '-buildVersion'),
                  xcode=output('xcodebuild', '-version'))
    sdks = ['iphoneos' if arguments.platform == 'ios' else 'iphonesimulator']
    if arguments.product in ('storage', 'compression'):
        sdks = ['iphoneos', 'iphonesimulator']
    for sdk in sdks:
        record[sdk] = [output('xcrun', '--sdk', sdk, '--show-sdk-version'),
                       output('xcrun', '--sdk', sdk, '--show-sdk-build-version'),
                       output('xcrun', '--sdk', sdk, 'clang', '--version')]
    tools = {'gpu': ['cmake', 'ninja', 'meson', 'bison', 'flex', 'pkg-config', 'gperf', 'python3'],
             'network': ['meson', 'ninja', 'pkg-config', 'python3'],
             'storage': ['gmake'], 'compression': ['cmake', 'ninja']}[arguments.product]
    record['tools'] = {tool: output(tool, '--version') for tool in tools}
    if arguments.product == 'gpu':
        record['python-packages'] = output('python3', '-c',
            'from importlib.metadata import version; print([(p,version(p)) for p in ["meson","Mako","PyYAML","packaging"]])')
    print(hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest())


if __name__ == '__main__':
    main()
