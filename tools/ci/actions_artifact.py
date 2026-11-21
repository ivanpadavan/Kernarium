#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Download an immutable Actions artifact and verify its server upload digest."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True, timeout=120))


def repository():
    return os.environ.get('GITHUB_REPOSITORY') or subprocess.check_output(
        ['gh', 'repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner'], text=True, timeout=60).strip()


def download(identifier, output):
    repo = repository()
    metadata = api(f'repos/{repo}/actions/artifacts/{identifier}')
    if metadata['expired']:
        raise RuntimeError('The selected Actions artifact has expired')
    origin = metadata['workflow_run']
    repo_id = int(os.environ.get('GITHUB_REPOSITORY_ID') or api(f'repos/{repo}')['id'])
    if origin['head_repository_id'] != repo_id:
        raise RuntimeError('The artifact was produced from another repository')
    digest = metadata.get('digest')
    if not digest or not digest.startswith('sha256:'):
        raise RuntimeError('An immutable artifact upload digest is required')
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'artifact.zip'
        with archive.open('wb') as destination:
            subprocess.run(['gh', 'api', f'repos/{repo}/actions/artifacts/{identifier}/zip'], stdout=destination, check=True, timeout=300)
        with archive.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != digest.split(':', 1)[1]:
                raise RuntimeError('Actions artifact upload digest mismatch')
        output.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as contents:
            for entry in contents.infolist():
                path = Path(entry.filename)
                if path.is_absolute() or '..' in path.parts:
                    raise RuntimeError('Unsafe artifact path')
            contents.extractall(output)
    metadata['web_url'] = f'https://github.com/{repo}/actions/runs/{origin["id"]}/artifacts/{identifier}'
    print('Verified Actions artifact:', metadata['name'], metadata['web_url'], flush=True)
    return metadata
