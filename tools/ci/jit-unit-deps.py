#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare the pinned native zlib required to configure ARM64 unit tests."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PREFIX = ROOT / '.build/dependencies/jit-unit-zlib'


def capture(*command):
    return subprocess.check_output(command, text=True, timeout=60).strip()


def run(*command, **kwargs):
    subprocess.run(command, check=True, timeout=600, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', action='store_true')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--input-key')
    args = parser.parse_args()
    rows = list(csv.reader((ROOT / 'dependencies/manifest.tsv').read_text().splitlines(), delimiter='\t'))
    header = next(row for row in rows if row and row[0] == 'name')
    row = next(row for row in rows if row and row[0] == 'zlib')
    if args.key:
        identity = dict(source=row, recipe=Path(__file__).read_text(), architecture=platform.machine(),
                        sdk=capture('xcrun', '--sdk', 'macosx', '--show-sdk-version'),
                        sdk_build=capture('xcrun', '--sdk', 'macosx', '--show-sdk-build-version'),
                        compiler=capture('xcrun', '--sdk', 'macosx', 'clang', '--version'),
                        cmake=capture('cmake', '--version'), ninja=capture('ninja', '--version'))
        print(hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest())
        return
    if not args.build or not args.input_key:
        parser.error('Use --key or --build --input-key KEY')
    work = ROOT / '.build/jit-unit-dependencies'
    work.mkdir(parents=True, exist_ok=True)
    manifest = work / 'manifest.tsv'
    with manifest.open('w') as output:
        writer = csv.writer(output, delimiter='\t', lineterminator='\n')
        writer.writerows([header, row])
    environment = {**os.environ, 'UML_DEPENDENCY_MANIFEST': str(manifest)}
    run(str(ROOT / 'dependencies/prepare.sh'), '--product', 'macos', env=environment)
    run(str(ROOT / 'dependencies/prepare.sh'), '--product', 'macos', '--verify', env=environment)
    run('cmake', '-S', str(ROOT / 'external/sources/zlib'), '-B', str(work / 'build'), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INSTALL_PREFIX=' + str(PREFIX),
        '-DCMAKE_OSX_SYSROOT=' + capture('xcrun', '--sdk', 'macosx', '--show-sdk-path'),
        '-DCMAKE_OSX_ARCHITECTURES=arm64', '-DCMAKE_OSX_DEPLOYMENT_TARGET=27.0',
        '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF', '-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF',
        '-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local;/opt/local',
        '-DZLIB_BUILD_SHARED=OFF', '-DZLIB_BUILD_STATIC=ON', '-DZLIB_BUILD_TESTING=OFF')
    run('cmake', '--build', str(work / 'build'), '--parallel', '3')
    run('cmake', '--install', str(work / 'build'))
    (PREFIX / 'source.json').write_text(json.dumps(dict(zip(header, row)), indent=2) + '\n')
    run(sys.executable, '-B', str(ROOT / 'tools/ci/debian-artifacts.py'), 'record',
        '--root', str(PREFIX), '--kind', 'jit-unit-zlib', '--key', args.input_key)


if __name__ == '__main__':
    main()
