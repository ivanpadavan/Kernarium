#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reuse a verified Node AOT/capsule/image set by its producer source inputs."""

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import actions_artifact

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/aot'))
import linux_node

spec = importlib.util.spec_from_file_location('debian_artifacts', ROOT / 'tools/ci/debian-artifacts.py')
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)

REQUIRED = (*linux_node.INPUTS, 'tools/ci/build-node-experiment.sh',
            'tools/ci/node-experiment.py', 'tools/ci/actions_artifact.py',
            'tools/ci/debian-artifacts.py', 'dependencies/prepare.sh',
            'tools/rootfs/producer.sh', 'tests/integration/linux/node_aot.js')
PATTERNS = (*REQUIRED, 'tools/rootfs/starter/**')


def fingerprint(tree):
    entries = {}
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        metadata, path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        name = path.decode()
        if kind == 'blob' and not name.endswith(('.md', '.rst')) and any(
                fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS):
            entries[name] = [mode, oid]
    missing = set(REQUIRED) - entries.keys()
    if missing:
        raise ValueError('Missing Node producer inputs: ' + ', '.join(sorted(missing)))
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def source_key():
    return fingerprint(subprocess.check_output(['git', 'ls-tree', '-rz', 'HEAD'], timeout=60))


def validate(root, key, capsule=False, producer_run=None):
    description = linux_node.verify(root / 'node/aot', ROOT)
    if capsule and artifacts.digest(root / 'node/node.erofs') != description['release']['system_partition_sha256']:
        raise ValueError('Reused Node capsule differs from its AOT identity')
    image = artifacts.verify(root / 'image', 'debian-phosh', key)
    if producer_run is not None and str(image['producer_run']) != str(producer_run):
        raise ValueError('Node image provenance names a different producer run')
    return description


def verify_origins(reference, aot, image):
    if any(item['workflow_run']['id'] != reference['workflow_run']['id'] for item in (aot, image)):
        raise ValueError('Node AOT and image must originate from the same producer run')


def choose(candidates, name, repository_id):
    eligible = [item for item in candidates if item['name'] == name and not item['expired']
                and item['workflow_run']['head_repository_id'] == repository_id]
    return max(eligible, key=lambda item: item['id'], default=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('key', 'restore', 'record'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--aot-artifact', type=int)
    parser.add_argument('--image-artifact', type=int)
    args = parser.parse_args()
    key = source_key()
    if args.operation == 'key':
        print(key)
        return
    if args.root is None:
        parser.error('--root is required')
    if args.operation == 'record':
        description = validate(args.root, key, capsule=True, producer_run=os.environ['GITHUB_RUN_ID'])
        if not args.aot_artifact or not args.image_artifact:
            parser.error('record requires both uploaded artifact IDs')
        reference = args.root / 'reuse'
        reference.mkdir()
        (reference / 'artifacts.json').write_text(json.dumps(dict(
            aot=args.aot_artifact, image=args.image_artifact,
            release=description['release']), sort_keys=True, indent=2) + '\n')
        artifacts.record(reference, 'node-experiment-reference', key)
        return
    repo = actions_artifact.repository()
    name = 'node-experiment-' + key
    listing = actions_artifact.api(f'repos/{repo}/actions/artifacts?name={name}&per_page=100')
    selected = choose(listing['artifacts'], name, int(os.environ['GITHUB_REPOSITORY_ID']))
    if selected:
        reference = args.root / 'reuse'
        metadata = actions_artifact.download(selected['id'], reference)
        origin = artifacts.verify(reference, 'node-experiment-reference', key)
        if str(origin['producer_run']) != str(metadata['workflow_run']['id']):
            raise ValueError('Node experiment provenance names a different producer run')
        pair = json.loads((reference / 'artifacts.json').read_text())
        aot = actions_artifact.download(pair['aot'], args.root / 'node/aot')
        image = actions_artifact.download(pair['image'], args.root / 'image')
        verify_origins(metadata, aot, image)
        description = validate(args.root, key, producer_run=metadata['workflow_run']['id'])
        if description['release'] != pair['release']:
            raise ValueError('Reused Node AOT has a different capsule identity')
        print('Reused complete Node AOT/image set:', metadata['web_url'], flush=True)
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write('reused=' + ('true' if selected else 'false') + '\n')


if __name__ == '__main__':
    main()
