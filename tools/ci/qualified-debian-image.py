#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fetch a qualified image, checking the release's origin and every asset hash."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def command(*args):
    return subprocess.check_output(args, text=True, timeout=300)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    parser.add_argument('--kind', default='phosh')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--test-draft', action='store_true', help='Explicit pairing runs may validate an unqualified draft')
    args = parser.parse_args()
    if args.test_draft and not args.tag:
        parser.error('--test-draft requires an explicit --tag')
    repository = command('gh', 'repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()
    releases = json.loads(command('gh', 'api', f'repos/{repository}/releases?per_page=100'))
    candidates = [r for r in releases if not r['draft'] and r['tag_name'].startswith('debian-13-' + args.kind + '-')]
    if args.tag:
        candidates = [r for r in releases if r['tag_name'] == args.tag]
    args.output.mkdir(parents=True, exist_ok=True)
    for release in candidates:
        assets = {a['name']: a for a in release['assets']}
        qualified = 'qualification.json' in assets
        if not qualified and not args.test_draft:
            continue
        tag = release['tag_name']
        if qualified:
            command('gh', 'release', 'download', tag, '--dir', str(args.output), '--pattern', 'qualification.json', '--clobber')
            qualification = json.loads((args.output / 'qualification.json').read_text())
            if qualification.get('kind') != 'debian-' + args.kind:
                continue
            for phase in ('write', 'read'):
                summary = qualification['tests'][phase]
                assert summary['totalTestCount'] == summary['passedTests'] == 1
                assert summary['failedTests'] == summary['skippedTests'] == 0
            assert qualification.get('validator_revision', qualification['source_revision']) == release['target_commitish']
        name = 'debian-13-' + args.kind + '.img'
        for asset in (name + '.zip', 'provenance.json', 'release-manifest.json'):
            command('gh', 'release', 'download', tag, '--dir', str(args.output), '--pattern', asset, '--clobber')
            expected = assets[asset]['digest']
            assert expected and expected.startswith('sha256:'), asset
            with (args.output / asset).open('rb') as source:
                assert hashlib.file_digest(source, 'sha256').hexdigest() == expected.split(':')[1]
        provenance = json.loads((args.output / 'provenance.json').read_text())
        if qualified:
            assert provenance['source_revision'] == qualification['source_revision']
            assert provenance['input_key'] == qualification['input_key']
        assert provenance['kind'] == 'debian-' + args.kind
        assert provenance['repository'] == repository
        for asset in (name + '.zip', 'release-manifest.json'):
            with (args.output / asset).open('rb') as source:
                assert hashlib.file_digest(source, 'sha256').hexdigest() == provenance['files'][asset]['sha256']
        expected = json.loads((args.output / 'release-manifest.json').read_text())['artifacts'][name]
        with zipfile.ZipFile(args.output / (name + '.zip')) as archive:
            assert archive.namelist() == [name]
            with archive.open(name) as source, (args.output / name).open('wb') as destination:
                # The simulator gets a sparse host file even after ZIP delivery.
                digest = hashlib.sha256()
                length = 0
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    length += len(block)
                    if block.strip(b'\0'):
                        destination.write(block)
                    else:
                        destination.seek(len(block), 1)
                destination.truncate(length)
                assert digest.hexdigest() == expected['sha256'] and length == expected['size']
        print(f'{"Qualified image" if qualified else "Explicit draft image"} {tag} from {provenance["source_revision"]}', flush=True)
        return
    raise RuntimeError('No qualified image with completed real write/read boot checks is available')


if __name__ == '__main__':
    main()
