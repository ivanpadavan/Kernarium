#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the existing device application with xtool's Xcode-project reader."""

import json
import os
from pathlib import Path
import subprocess
import zipfile


def main():
    root = Path.cwd()
    project = root / 'Kernarium/Kernarium.xcodeproj/project.pbxproj'
    original = project.read_text()
    # The Linux experiment changes build settings only in its disposable CI
    # checkout. Every source, package pin and build phase is still the project.
    settings = ('CODE_SIGNING_ALLOWED = NO;\n'
                '\t\t\t\tCODE_SIGNING_REQUIRED = NO;\n'
                '\t\t\t\tSWIFT_ACTIVE_COMPILATION_CONDITIONS = "$(inherited) KERNARIUM_NODE_AOT";\n'
                f'\t\t\t\tCURRENT_PROJECT_VERSION = {os.environ["GITHUB_RUN_NUMBER"]};\n')
    marker = 'ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;'
    if original.count(marker) != 2:
        raise ValueError('Unrecognized application build configurations')
    # xtool has no Linux actool. Only the empty Xcode template may be omitted;
    # real artwork or color values require an asset compiler, and fail here.
    assets = root / 'Kernarium/Kernarium/Assets.xcassets'
    for path in assets.rglob('*'):
        if path.is_file():
            if path.name != 'Contents.json':
                raise ValueError('The Linux build needs an asset compiler for ' + str(path))
            value = json.loads(path.read_text())
            if any('filename' in entry or 'color' in entry
                   for key in ('images', 'colors') for entry in value.get(key, [])):
                raise ValueError('The Linux build needs an asset compiler for ' + str(path))
    source = original.replace(marker, settings + '\t\t\t\t' + marker)
    configuration = root / 'Kernarium/xtool.yml'
    if configuration.exists():
        raise ValueError('xtool configuration already exists')
    source_assets = assets.with_name('Assets.xtool-empty')
    assets.rename(source_assets)
    # A directory inside the synchronized source group would still be a
    # resource, so move the empty template outside that group for the build.
    saved_assets = root / '.build/xtool-empty-assets'
    source_assets.rename(saved_assets)
    try:
        project.write_text(source)
        configuration.write_text('version: 1\nproject: Kernarium.xcodeproj\nscheme: Kernarium\n')
        subprocess.run(['xtool', 'dev', 'build', '--configuration', 'release',
                        '--triple', 'arm64-apple-ios26.0', '--ipa', '--verbose'],
                       cwd=root / 'Kernarium', stdin=subprocess.DEVNULL,
                       check=True, timeout=7200)
    finally:
        project.write_text(original)
        configuration.unlink(missing_ok=True)
        saved_assets.rename(assets)
    output = root / 'Kernarium/xtool/Kernarium.ipa'
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        for name in ('Kernarium', 'Frameworks/KRuntime_6_18_4K.framework/KRuntime_6_18_4K',
                     'Frameworks/UmlArm64Aot.framework/UmlArm64Aot',
                     'Frameworks/UmlArm64Aot.framework/licences.json'):
            if 'Payload/Kernarium.app/' + name not in names:
                raise ValueError('The IPA is missing ' + name)
    destination = Path(os.environ['RUNNER_TEMP']) / 'Kernarium-Debian.ipa'
    destination.write_bytes(output.read_bytes())


if __name__ == '__main__':
    main()
