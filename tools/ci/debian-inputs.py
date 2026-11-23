#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Debian product lanes and fingerprint their actual source inputs."""

import argparse
import csv
import fnmatch
import functools
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess


GROUPS = {
    'debian-base': ['tools/rootfs/starter/build-all.sh',
                    'tools/rootfs/starter/products/debian-base.sh',
                    'tools/rootfs/starter/guest/**', 'tools/rootfs/starter/verify-debian-base.py',
                    'tools/rootfs/producer.sh'],
    'phosh': ['tools/rootfs/starter/products/debian-phosh.sh',
              'tools/rootfs/starter/products/debian-phosh/**',
              'tools/rootfs/starter/verify-phosh.py'],
    'minimal': ['tools/rootfs/starter/products/debian-minimal.sh'],
    'image-tools': ['tools/rootfs/starter/make-gpt-image.py',
                    'tools/rootfs/starter/gpt.py', 'tools/rootfs/starter/write-manifest.py'],
    'boot-base': ['tools/rootfs/starter/buildroot/**', 'tools/rootfs/producer.sh'],
    'kernel': ['kernel/common/abi/**', 'kernel/common/config/**',
               'kernel/common/artifacts/**', 'kernel/mainline-6.18/config/**',
               'kernel/mainline-6.18/build/**',
               'kernel/mainline-6.18/patches/metadata.env',
               'kernel/mainline-6.18/patches/series.tsv'],
    'runtime': ['darwin/**', 'cmake/**', 'CMakeLists.txt', 'CMakePresets.json',
                'Kernarium/Runtime/**', 'Kernarium/scripts/package-ios-frameworks.sh',
                'tools/ci/build-apple-runtime.sh',
                'tools/rootfs/boot-ramdisk/init', 'tools/rootfs/boot-ramdisk/boot.env',
                'scripts/kernel-dsym-symbols.py', 'scripts/check-no-aot-diagnostics.py',
                'scripts/check-software-renderer.py'],
    'app': ['Kernarium/Kernarium/**', 'GuestHost/**',
            'Kernarium/Kernarium.xcodeproj/**', 'Kernarium/scripts/**'],
    'app-tests': ['Kernarium/KernariumTests/**', 'Kernarium/KernariumUITests/**'],
    'jit-ci': ['tools/ci/jit-unit-deps.py'],
    'app-ci': ['.github/workflows/debian-app.yml', '.github/actions/apple-runtime/**',
               '.github/actions/xtool-runtime/**', 'tools/ci/xtool/**',
               'tools/ci/apple-product-key.py', 'tools/ci/debian-artifacts.py',
               'tools/ci/stage-debian-runtime.py', 'tools/ci/qualified-debian-image.py',
               'tools/ci/actions_artifact.py', 'tools/ci/fetch-debian-app.py',
               'tools/ci/run-debian-simulator.py', 'tools/ci/debian-inputs.py'],
    'image-ci': ['.github/workflows/debian-phosh-image.yml', '.github/workflows/debian-image-product.yml',
                 'tools/ci/debian-artifacts.py', 'tools/ci/debian-image.sh',
                 'tools/ci/run-debian-simulator.py',
                 'tools/ci/actions_artifact.py',
                 'tools/ci/debian-inputs.py'],
}

DEPENDENCIES = {
    'gpu': ('apple-gpu-vulkan-experiment', [
        'dependencies/build-apple-gpu*.sh', 'dependencies/build-common.sh',
        'dependencies/bison2-grammar-shim.sh', 'dependencies/apple-gpu/**',
        'dependencies/cmake/VerifyAppleGpuPrefix.cmake']),
    'network': ('ios-network', ['dependencies/build-ios-network.sh',
        'dependencies/build-common.sh', 'dependencies/ios-network/**',
        'dependencies/cmake/VerifyIosNetworkPrefix.cmake']),
    'storage': ('ios-storage', ['dependencies/build-ios-storage.sh',
        'dependencies/ios-static-xcframework.sh', 'dependencies/gptfdisk/**',
        'dependencies/cmake/VerifyIosStorageXCFramework.cmake']),
    'compression': ('ios-compression', ['dependencies/build-ios-zstd.sh',
        'dependencies/ios-static-xcframework.sh', 'dependencies/zstd/**',
        'dependencies/cmake/VerifyIosZstdXCFramework.cmake']),
}


class Repository:
    def __init__(self, ref='HEAD'):
        self.ref = ref
        self.paths = subprocess.check_output(
            ['git', 'ls-tree', '-r', '--name-only', ref], text=True).splitlines()

    @functools.cache
    def read(self, path):
        return subprocess.check_output(['git', 'show', f'{self.ref}:{path}'])

    def files(self, patterns):
        return sorted(path for path in self.paths
                      if not path.endswith(('.md', '.rst')) and
                      any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns))

    def fingerprint(self, patterns):
        entries = {path: self.read(path) for path in self.files(patterns)}
        return hash_entries(entries)

    def dependency(self, name, platform):
        product, patterns = DEPENDENCIES[name]
        family = 'ios-device' if platform == 'ios' else 'ios-simulator'
        contract_name = 'gpu-*' if name == 'gpu' else name + '-*'
        patterns = patterns + [f'dependencies/platforms/{family}-{contract_name}',
                               'dependencies/cmake/VerifyManifestProduct.cmake',
                               'dependencies/cmake/VerifyPrefix.cmake']
        if name in ('storage', 'compression'):
            patterns += [f'dependencies/platforms/ios-*-{contract_name}',
                         'dependencies/cmake/VerifyIosStaticXCFramework.cmake']
        if name == 'gpu' and platform == 'ios-simulator':
            patterns += ['dependencies/llvm/**']
        entries = {path: self.read(path) for path in self.files(patterns)}
        entries.update(self.product_sources(product))
        return hash_entries(entries)

    def product_sources(self, product):
        entries = {}
        rows = []
        for row in csv.reader(io.StringIO(self.read('dependencies/manifest.tsv').decode()), delimiter='\t'):
            if not row or row[0].startswith('#') or product not in row[2].split(','):
                continue
            rows.append(row[:2] + row[3:])
            for series in row[6].split('+'):
                if series == '-':
                    continue
                if series.startswith('rootfs-vulkan:'):
                    series = 'tools/rootfs/vulkan/' + series.split(':', 1)[1]
                else:
                    series = 'dependencies/' + series
                contents = self.read(series)
                tree = [line for line in contents.splitlines() if line.startswith(b'tree\t')]
                if not tree:
                    raise ValueError(f'{series}: a generated result tree is required')
                entries[series] = b'\n'.join(tree)
        entries['manifest-product:' + product] = json.dumps(rows, separators=(',', ':')).encode()
        return entries

    def sources(self, platform):
        entries = {path: self.read(path) for path in self.files([
            'dependencies/prepare.sh', 'scripts/patch_series.py'])}
        for product, _ in DEPENDENCIES.values():
            entries.update(self.product_sources(product))
        if platform == 'ios-simulator':
            entries['llvm-archives'] = self.read('dependencies/llvm/archives.tsv')
        return hash_entries(entries)

    def identity(self, name):
        if name in ('phosh', 'minimal'):
            return hash_entries({group: self.fingerprint(GROUPS[group]).encode()
                                 for group in ('debian-base', name, 'image-tools')})
        if name != 'boot-base':
            return self.fingerprint(GROUPS[name])
        # Debian product dispatch and package recipes do not build the boot
        # archive. Keep its existing recipe identity across product splits.
        driver = self.read('tools/rootfs/starter/build-all.sh')
        boot_inputs = {path: self.read(path) for path in self.files(GROUPS['boot-base'])}
        for name in ('download_checked', 'artifact_path', 'prepare_busybox_config', 'build_boot_base'):
            function = re.search(rb'^' + name.encode() + rb'\(\)\n\{\n.*?^\}\n', driver, re.M | re.S)
            if not function:
                raise ValueError(f'Missing boot-base recipe function: {name}')
            boot_inputs[name] = function.group()
        boot_inputs['pins'] = b'\n'.join(line for line in driver.splitlines()
                                       if line.startswith((b'BUILDROOT_', b'BUSYBOX_')))
        return hash_entries(boot_inputs)

    def identities(self):
        identities = {name: self.identity(name) for name in GROUPS}
        for name in DEPENDENCIES:
            for platform in ('ios', 'ios-simulator'):
                identities[f'{name}-{platform}'] = self.dependency(name, platform)
        return identities


def hash_entries(entries):
    digest = hashlib.sha256()
    for name, contents in sorted(entries.items()):
        digest.update(name.encode() + b'\0' + contents + b'\0')
    return digest.hexdigest()


def plan(before, after):
    changed = {name for name in after if before.get(name) != after[name]}
    kernel = 'kernel' in changed
    boot_base = 'boot-base' in changed
    app = bool(changed & {'app', 'app-tests', 'app-ci', 'runtime', 'kernel', 'boot-base'}
               or any(name.startswith(tuple(DEPENDENCIES)) for name in changed))
    images = [name for name in ('phosh', 'minimal')
              if name in changed or 'image-ci' in changed or kernel or boot_base]
    return dict(app=app, kernel=kernel, boot_base=boot_base, images=images,
                runtime_tests=bool(changed & {'runtime', 'jit-ci'}),
                base=bool(images), changed=sorted(changed), identities=after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before')
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--component')
    arguments = parser.parse_args()
    repository = Repository(arguments.ref)
    if arguments.component:
        if arguments.component.startswith('sources-'):
            print(repository.sources(arguments.component.removeprefix('sources-')))
        elif arguments.component in GROUPS:
            print(repository.identity(arguments.component))
        else:
            print(repository.identities()[arguments.component])
        return
    after = repository.identities()
    before = Repository(arguments.before).identities() if arguments.before and set(arguments.before) != {'0'} else {}
    result = plan(before, after)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if arguments.output:
        arguments.output.write_text(encoded + '\n')
    if arguments.github_output:
        with arguments.github_output.open('a') as output:
            for key in ('app', 'kernel', 'boot_base', 'base', 'runtime_tests'):
                output.write(f'{key}={str(result[key]).lower()}\n')
            output.write('images=' + json.dumps(result['images']) + '\n')
            for key, identity in after.items():
                output.write(f'{key.replace("-", "_")}_key={identity}\n')


if __name__ == '__main__':
    main()
