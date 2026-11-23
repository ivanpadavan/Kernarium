#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Copy the SDK inputs xtool consumes from an installed Xcode, without building."""

import argparse
from pathlib import Path
import shutil
import subprocess


def export(developer, output):
    destination = output / 'Xcode.app/Contents/Developer'
    if output.exists():
        raise ValueError('SDK export destination already exists')
    paths = [Path('Toolchains/XcodeDefault.xctoolchain/usr/lib') / name
             for name in ('swift', 'swift_static', 'clang')]
    for platform in ('iPhoneOS', 'iPhoneSimulator', 'MacOSX'):
        root = Path('Platforms') / (platform + '.platform')
        paths += [root / 'Info.plist']
        paths += [root / 'Developer' / name for name in
                  ('SDKs', 'Library/Frameworks', 'Library/PrivateFrameworks', 'usr/lib')]
    for relative in paths:
        source = developer / relative
        target = destination / relative
        if not source.exists():
            # Some Xcode platforms do not carry private frameworks or usr/lib.
            if relative.name in ('PrivateFrameworks', 'lib'):
                continue
            raise ValueError(f'Missing SDK input: {relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)
    (output / 'xcode-version.txt').write_text(subprocess.check_output(
        ['xcodebuild', '-version'], text=True, timeout=30))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--developer', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    export(args.developer, args.output)
