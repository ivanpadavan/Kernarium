#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify a chosen IPA and its matching prebuilt Simulator host for pairing tests."""
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
    parser.add_argument('--tag', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    release = json.loads(command('gh', 'api', 'repos/{owner}/{repo}/releases/tags/' + args.tag))
    assets = {a['name']: a for a in release['assets']}
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ('Kernarium-Debian.ipa', 'debian-app-test-host.tar.gz', 'provenance.json'):
        command('gh', 'release', 'download', args.tag, '--dir', str(args.output), '--pattern', name)
        # The API exposes the server's upload digest for draft assets too.
        expected = assets[name]['digest']
        assert expected and expected.startswith('sha256:')
        with (args.output / name).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == expected.split(':')[1], name
    with zipfile.ZipFile(args.output / 'Kernarium-Debian.ipa') as archive:
        assert archive.testzip() is None
        assert 'Payload/Kernarium.app/Kernarium' in archive.namelist()
    provenance = json.loads((args.output / 'provenance.json').read_text())
    assert provenance['kind'] == 'ipa'
    assert provenance['source_revision'] == release['target_commitish']
    with (args.output / 'Kernarium-Debian.ipa').open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == provenance['files']['Kernarium-Debian.ipa']['sha256']
    subprocess.run(['tar', '-C', str(args.output), '-xzf', str(args.output / 'debian-app-test-host.tar.gz')], check=True, timeout=300)
    host = json.loads((args.output / 'Products/provenance.json').read_text())
    assert host['source_revision'] == release['target_commitish']
    assert host['kind'] == 'app-test-host'
    subprocess.run(['python3', '-B', str(Path(__file__).with_name('debian-artifacts.py')), 'verify', '--root', str(args.output / 'Products'), '--kind', 'app-test-host'], check=True, timeout=120)
    print('Verified chosen IPA and Simulator sibling from', host['source_revision'])


if __name__ == '__main__':
    main()
