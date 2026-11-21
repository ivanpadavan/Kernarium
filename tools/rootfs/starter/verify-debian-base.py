#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify the shared Debian package base before extracting it."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def verify(archive):
    manifest = json.loads((archive.parent / 'release-manifest.json').read_text())
    record = manifest['artifacts'][archive.name]
    if archive.stat().st_size != record['size']:
        raise ValueError('Debian base archive size mismatch')
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != record['sha256']:
            raise ValueError('Debian base archive digest mismatch')
    with tarfile.open(archive) as contents:
        names = {entry.name.removeprefix('./') for entry in contents}
        for required in ('usr/bin/python3', 'usr/bin/tmux', 'usr/lib/systemd/systemd', 'var/lib/dpkg/status'):
            if required not in names:
                raise ValueError(f'Debian base is missing {required}')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.archive), sort_keys=True))
