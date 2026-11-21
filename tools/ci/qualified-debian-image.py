#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select a boot-qualified Actions IMG without rebuilding or publishing it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import zipfile

from actions_artifact import api, download, repository

spec = importlib.util.spec_from_file_location('debian_artifacts', Path(__file__).with_name('debian-artifacts.py'))
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-id', type=int)
    parser.add_argument('--kind', default='phosh', choices=['phosh', 'minimal'])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    identifier = args.artifact_id
    deadline = time.monotonic() + args.wait_seconds
    while identifier is None:
        candidates = api(f'repos/{repository()}/actions/artifacts?name=qualified-image-{args.kind}&per_page=100')['artifacts']
        candidates = [a for a in candidates if not a['expired']]
        if candidates:
            identifier = max(candidates, key=lambda a: a['id'])['id']
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('No boot-qualified IMG artifact is available')
        print('Waiting for an independently produced boot-qualified IMG artifact', flush=True)
        time.sleep(min(20, max(0, deadline - time.monotonic())))
    metadata = download(identifier, args.output)
    wrapper = artifacts.verify(args.output, kind='qualified-debian-' + args.kind)
    assert wrapper['source_revision'] == metadata['workflow_run']['head_sha']
    image_root = args.output / 'image'
    producer = artifacts.verify(image_root, kind='debian-' + args.kind, key=wrapper['input_key'])
    qualification = json.loads((args.output / 'qualification.json').read_text())
    assert qualification['source_revision'] == producer['source_revision']
    assert qualification['validator_revision'] == wrapper['source_revision']
    assert qualification['input_key'] == wrapper['input_key']
    for phase in ('write', 'read'):
        summary = qualification['tests'][phase]
        assert summary['totalTestCount'] == summary['passedTests'] == 1
        assert summary['failedTests'] == summary['skippedTests'] == 0
    name = f'debian-13-{args.kind}.img'
    expected = json.loads((image_root / 'release-manifest.json').read_text())['artifacts'][name]
    with zipfile.ZipFile(image_root / (name + '.zip')) as archive:
        assert archive.namelist() == [name]
        with archive.open(name) as source, (args.output / name).open('wb') as destination:
            digest, length = hashlib.sha256(), 0
            while block := source.read(1024 * 1024):
                digest.update(block)
                length += len(block)
                if block.strip(b'\0'):
                    destination.write(block)
                else:
                    destination.seek(len(block), 1)
            destination.truncate(length)
            assert digest.hexdigest() == expected['sha256'] and length == expected['size']
    (args.output / 'selected-image.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('Qualified IMG from:', producer['source_revision'], flush=True)


if __name__ == '__main__':
    main()
