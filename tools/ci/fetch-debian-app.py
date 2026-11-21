#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify an existing Actions IPA and its matching Simulator host for pairing tests."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import zipfile

from actions_artifact import api, download, repository

spec = importlib.util.spec_from_file_location('debian_artifacts', Path(__file__).with_name('debian-artifacts.py'))
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    available = api(f'repos/{repository()}/actions/runs/{args.run_id}/artifacts')['artifacts']
    selected = {a['name']: a for a in available if not a['expired']}
    device = download(selected['app-device']['id'], args.output / 'device')
    simulator = download(selected['app-simulator']['id'], args.output / 'simulator')
    assert device['workflow_run']['head_sha'] == simulator['workflow_run']['head_sha']
    revision = device['workflow_run']['head_sha']
    artifacts.verify(args.output / 'device', kind='ipa', key=revision)
    with zipfile.ZipFile(args.output / 'device/Kernarium-Debian.ipa') as archive:
        assert archive.testzip() is None
        assert 'Payload/Kernarium.app/Kernarium' in archive.namelist()
    subprocess.run(['tar', '-C', str(args.output), '-xzf', str(args.output / 'simulator/debian-app-test-host.tar.gz')], check=True, timeout=300)
    artifacts.verify(args.output / 'Products', kind='app-test-host', key=revision)
    print('Verified IPA and matching Simulator host from', revision, flush=True)


if __name__ == '__main__':
    main()
