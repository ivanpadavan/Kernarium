#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record and verify source provenance and content hashes of Debian CI assets."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

MANIFEST = 'provenance.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def contents(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        if path.is_symlink():
            result[name] = {'link': os.readlink(path)}
        elif path.is_file():
            result[name] = {'sha256': digest(path), 'size': path.stat().st_size}
    if not result:
        raise ValueError('An empty artifact cannot be recorded or verified')
    return result


def record(root, kind, key):
    revision = os.environ.get('GITHUB_SHA') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    result = dict(version=1, kind=kind, input_key=key, source_revision=revision,
                  repository=os.environ['GITHUB_REPOSITORY'],
                  producer_run=os.environ.get('GITHUB_RUN_ID'), files=contents(root))
    (root / MANIFEST).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def verify(root, kind=None, key=None):
    result = json.loads((root / MANIFEST).read_text())
    if result['version'] != 1 or result['repository'] != os.environ['GITHUB_REPOSITORY']:
        raise ValueError('Unrecognized artifact provenance')
    if not re.fullmatch('[0-9a-f]{40}', result['source_revision']):
        raise ValueError('An exact producer source revision is required')
    if not result.get('producer_run') or not str(result['producer_run']).isdigit():
        raise ValueError('A GitHub Actions producer run is required')
    if kind is not None and result['kind'] != kind:
        raise ValueError('Artifact product does not match its consumer')
    if key is not None and result['input_key'] != key:
        raise ValueError('Artifact source inputs do not match its consumer')
    if result['files'] != contents(root):
        raise ValueError('Artifact contents do not match their recorded hashes')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['record', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--kind')
    parser.add_argument('--key')
    arguments = parser.parse_args()
    if arguments.operation == 'record':
        if not arguments.kind or not arguments.key:
            parser.error('record requires --kind and --key')
        result = record(arguments.root, arguments.kind, arguments.key)
    else:
        result = verify(arguments.root, arguments.kind, arguments.key)
    print(json.dumps({key: value for key, value in result.items() if key != 'files'}, sort_keys=True))


if __name__ == '__main__':
    main()
