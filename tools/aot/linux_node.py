#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the fixed Linux Node capsule and its Kernarium AOT artifact in CI."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

import elf64
import release_artifact as artifact
import rewrite

VERSION = "22.16.0"
ARCHIVE = "node-v22.16.0-linux-arm64.tar.xz"
ARCHIVE_SHA256 = "eab80cb88f8fda1e65f5e8d0420c9809bdb320b03fd34976ab7161b6e703b910"
PREFIX = "/opt/kernarium-node"
INPUTS = tuple(path for path in artifact.GENERATOR_INPUTS if path not in (
    "tools/aot/libraries.txt", "tools/aot/extract_image.py", "tools/aot/licences.py")) + (
    "tools/aot/linux_node.py", "tools/aot/linux-node-archives.tsv")


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def extract_npm(source, root):
    prefix = f'node-v{VERSION}-linux-arm64/'
    npm_prefix = prefix + 'lib/node_modules/npm'
    for entry in source.getmembers():
        if entry.name != npm_prefix and not entry.name.startswith(npm_prefix + '/'):
            continue
        relative = Path(entry.name[len(prefix):])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe bundled npm path')
        destination = root / relative
        if entry.isdir():
            destination.mkdir(parents=True, exist_ok=True)
        elif entry.isfile():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(entry) as stream, destination.open('wb') as target:
                shutil.copyfileobj(stream, target)
            destination.chmod(0o755 if entry.mode & 0o111 else 0o644)
        else:
            raise ValueError('Not a regular bundled npm file or directory')
    for directory in root.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o755)
    package = json.loads((root / 'lib/node_modules/npm/package.json').read_text())
    if package.get('name') != 'npm' or not isinstance(package.get('version'), str):
        raise ValueError('Bundled npm has no package identity')
    return package['version']


def generator(repo):
    return {name: artifact.sha256_file(repo / name) for name in INPUTS}


def verify(directory: Path, repo: Path):
    result = artifact.verify(directory, repo=repo, generator=False)
    if not result['release']['release_id'].startswith('linux-node-22.16.0-'):
        raise ValueError("Not the fixed Linux Node experiment")
    if result['generator'] != generator(repo):
        raise ValueError("Linux Node AOT generator differs from the source tree")
    if result['blob']['version'] != rewrite.BLOB_VERSION:
        raise ValueError("Linux Node AOT layout version differs from the runtime")
    manifest = artifact.load_json(directory / 'manifest.json')
    if not manifest.get('libraries') or manifest.get('refused'):
        raise ValueError("Node was not rewritten")
    return result


def build(archive: Path, output: Path, repo: Path):
    if artifact.sha256_file(archive) != ARCHIVE_SHA256:
        raise ValueError("Node archive differs from its pinned SHA-256")
    output.mkdir(parents=True, exist_ok=False)
    aot = output / 'aot'
    aot.mkdir()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # TemporaryDirectory is private (0700); its root becomes the guest's
        # mount root, which every guest user must be able to traverse.
        root.chmod(0o755)
        node = root / 'bin/node'
        node.parent.mkdir()
        node.parent.chmod(0o755)
        with tarfile.open(archive) as source:
            for member, destination in ((f'node-v{VERSION}-linux-arm64/bin/node', node),
                                        (f'node-v{VERSION}-linux-arm64/LICENSE', root / 'LICENSE')):
                entry = source.getmember(member)
                if not entry.isfile():
                    raise ValueError(f"Not a regular archive member: {member}")
                with source.extractfile(entry) as stream, destination.open('wb') as target:
                    shutil.copyfileobj(stream, target)
                destination.chmod(0o644)
            npm_version = extract_npm(source, root)
        node.chmod(0o755)
        identity = dict(version=VERSION, npm_version=npm_version, archive_sha256=ARCHIVE_SHA256,
                        node_sha256=artifact.sha256_file(node))
        identity_bytes = encoded(identity)
        (root / 'identity.json').write_bytes(identity_bytes)
        (root / 'identity.json').chmod(0o644)
        capsule = output / 'node.erofs'
        subprocess.run(['mkfs.erofs', '--all-root', str(capsule), str(root)], check=True, timeout=300)
        release = dict(release_id='linux-node-' + VERSION + '-' + identity['node_sha256'][:12],
                       release_manifest_sha256=hashlib.sha256(identity_bytes).hexdigest(),
                       system_partition_sha256=artifact.sha256_file(capsule))
        elf = elf64.Elf(node.read_bytes())
        segment = elf.executable_segment()
        if segment.vaddr % 4096:
            raise ValueError("Node executable segment is not page aligned")
        end = (segment.vaddr + segment.filesz + 4095) & ~4095
        units = []
        # Bound each entry table without changing the guest file or addresses.
        window = 1024 * 1024
        for index, begin in enumerate(range(segment.vaddr, end, window)):
            unit = rewrite.build_unit((index, PREFIX + '/bin/node@' + str(index),
                                       str(node), False, begin,
                                       min(begin + window, end), True))
            if 'refused' in unit:
                raise ValueError(unit['refused'])
            units.append(unit)
        if not sum(unit['stats']['inlined'] for unit in units):
            raise ValueError("Node has no rewritten instructions")
        blob, manifest = rewrite.build_blob(units, rewrite.read_layout(rewrite.LAYOUT_HEADER),
            release['release_id'], manifest_sha256=release['release_manifest_sha256'],
            system_sha256=release['system_partition_sha256'])
        (aot / 'blob.bin').write_bytes(blob)
        (aot / 'manifest.json').write_bytes(encoded(manifest))
        (aot / 'licences.json').write_bytes(encoded(dict(release_id=release['release_id'],
            libraries=[dict(path=PREFIX + '/bin/node', labels=['Node.js and bundled dependencies'],
                            notices=[dict(id='node-license', text=(root / 'LICENSE').read_text())])])))
        description = dict(schema=artifact.ARTIFACT_SCHEMA, release=release,
            blob=dict(bytes=len(blob), sha256=artifact.sha256_file(aot / 'blob.bin'),
                      magic=artifact.BLOB_MAGIC.decode(), version=rewrite.BLOB_VERSION),
            files={name: artifact.sha256_file(aot / name) for name in ('manifest.json', 'licences.json')},
            libraries=dict(library=[unit['path'] for unit in units], boot=[]), generator=generator(repo))
        (aot / 'artifact.json').write_bytes(encoded(description))
        verify(aot, repo)


def install(output: Path, root: Path):
    """Install into an unbooted Debian root; the guest mounts EROFS itself."""
    description = verify(output / 'aot', Path(__file__).resolve().parents[2])
    if artifact.sha256_file(output / 'node.erofs') != description['release']['system_partition_sha256']:
        raise ValueError("Node capsule differs from its AOT artifact")
    image = root / 'usr/lib/kernarium/node.erofs'
    image.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output / 'node.erofs', image)
    (root / PREFIX.lstrip('/')).mkdir(parents=True, exist_ok=True)
    with (root / 'etc/fstab').open('a') as target:
        target.write('\n/usr/lib/kernarium/node.erofs /opt/kernarium-node erofs loop,ro 0 0\n')
    wrapper = root / 'usr/local/bin/node'
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text('#!/bin/sh\nexec /opt/kernarium-node/bin/node --jitless "$@"\n')
    wrapper.chmod(0o755)
    for tool in ('npm', 'npx'):
        wrapper = root / 'usr/local/bin' / tool
        wrapper.write_text('#!/bin/sh\n'
            'if [ -z "${npm_config_prefix+x}${NPM_CONFIG_PREFIX+x}" ]; then\n'
            '    export npm_config_prefix=/usr/local\n'
            'fi\n'
            f'exec {PREFIX}/bin/node --jitless {PREFIX}/lib/node_modules/npm/bin/{tool}-cli.js "$@"\n')
        wrapper.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['build', 'verify', 'install'])
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if args.operation == 'build':
        build(args.archive, args.output, repo)
    elif args.operation == 'verify':
        verify(args.output, repo)
    else:
        install(args.output, args.root)


if __name__ == '__main__':
    main()
