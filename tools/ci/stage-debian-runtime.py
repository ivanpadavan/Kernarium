#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage accepted kernel and boot artifacts without rebuilding either product."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess

spec = importlib.util.spec_from_file_location('debian_inputs', Path(__file__).with_name('debian-inputs.py'))
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


def run(*args):
    subprocess.run(args, check=True, timeout=300)


def main():
    origin = '32e5dbbf7a135652bfb17cfc522ed0c98cd58c3f'
    for component in ('kernel', 'boot-base'):
        current = inputs.Repository().identities()[component]
        accepted = inputs.Repository(origin).identities()[component]
        if current != accepted:
            raise RuntimeError(f'The accepted {component} artifact has different source inputs; accept a replacement first')
    download = Path('.build/accepted-inputs')
    download.mkdir(parents=True, exist_ok=True)
    run('gh', 'release', 'download', 'debian-44931022698-1', '--dir', str(download),
        '--pattern', 'linux-kernel-ci.tar.gz', '--pattern', 'accepted-aarch64.env', '--pattern', 'uml-boot-base-aarch64.cpio.gz')
    digests = {
        'accepted-aarch64.env': '1455163fab5016f7b22a08af8f845bdf85c8eb3a248d45df036316c399f05b80',
        'linux-kernel-ci.tar.gz': '2267960453985ac83886aa786b1c456ded1795de0c48f24bbc53fa4bbed96563',
        'uml-boot-base-aarch64.cpio.gz': '461354b889235e50e185e4843c5a98d29aa26a2b2712084a435140bbee1bde31',
    }
    for name, digest in digests.items():
        with (download / name).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == digest, name
    kernel = Path('.build/kernel/artifacts')
    kernel.mkdir(parents=True, exist_ok=True)
    run('tar', '-C', str(kernel), '-xzf', str(download / 'linux-kernel-ci.tar.gz'))
    descriptors = list(kernel.glob('*/kernel-artifact.json'))
    assert len(descriptors) == 1, descriptors
    artifact = descriptors[0].parent.resolve()
    run('python3', '-B', 'kernel/common/artifacts/verify-kernel-artifact.py', '--artifact', str(artifact),
        '--common-root', 'kernel/common', '--abi-metadata', 'kernel/common/abi/identity.env',
        '--kernel-metadata', 'kernel/mainline-6.18/patches/metadata.env')
    descriptor = Path('tools/rootfs/boot-ramdisk/accepted-aarch64.env')
    descriptor.write_bytes((download / descriptor.name).read_bytes())
    values = dict(line.split('=', 1) for line in descriptor.read_text().splitlines() if line and not line.startswith('#'))
    archive = Path('.build/rootfs') / values['UML_BOOT_BASE_RELEASE'] / 'uml-boot-base-aarch64.cpio.gz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes((download / archive.name).read_bytes())
    run('python3', '-B', 'tools/rootfs/boot-ramdisk/verify_base.py', '--descriptor', str(descriptor), '--archive', str(archive))
    with Path(os.environ['GITHUB_ENV']).open('a') as env:
        env.write(f'UML_MAINLINE_618_KERNEL_ARTIFACT_DIR={artifact}\n')
        env.write('CI_KERNEL_SHA256=' + digests['linux-kernel-ci.tar.gz'] + '\n')
        env.write('CI_BOOT_BASE_SHA256=' + digests['uml-boot-base-aarch64.cpio.gz'] + '\n')


if __name__ == '__main__':
    main()
