#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Node snapshots follow producer inputs and retain integrity and pair identity."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/ci'))
spec = importlib.util.spec_from_file_location('node_experiment', ROOT / 'tools/ci/node-experiment.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def tree(changes=None):
    entries = {path: ('100644', 'a' * 40) for path in experiment.REQUIRED}
    entries['tools/rootfs/starter/guest/session.py'] = ('100755', 'b' * 40)
    entries.update(changes or {})
    return b'\0'.join(f'{mode} blob {oid}\t{path}'.encode()
                      for path, (mode, oid) in sorted(entries.items())) + b'\0'


class Reuse(unittest.TestCase):
    def test_app_and_documentation_changes_reuse_the_guest_snapshot(self):
        before = experiment.fingerprint(tree())
        after = experiment.fingerprint(tree({
            'Kernarium/Kernarium/ContentView.swift': ('100644', 'c' * 40),
            'docs/architecture.md': ('100644', 'd' * 40)}))
        self.assertEqual(before, after)

    def test_guest_bytes_and_executable_modes_invalidate_the_snapshot(self):
        before = experiment.fingerprint(tree())
        for change in ({'tools/aot/linux_node.py': ('100644', 'c' * 40)},
                       {'tools/rootfs/starter/guest/session.py': ('100644', 'b' * 40)}):
            with self.subTest(change=change):
                self.assertNotEqual(before, experiment.fingerprint(tree(change)))

    def test_selection_requires_matching_inputs_and_repository(self):
        def artifact(identifier, name='expected', repository=1, expired=False):
            return dict(id=identifier, name=name, expired=expired,
                        workflow_run={'head_repository_id': repository})
        chosen = experiment.choose([artifact(1), artifact(2), artifact(3, expired=True),
                                    artifact(4, repository=2), artifact(5, name='other-inputs')],
                                   'expected', 1)
        self.assertEqual(chosen['id'], 2)

    def test_different_producer_runs_cannot_form_a_pair(self):
        def origin(identifier):
            return {'workflow_run': {'id': identifier}}
        with self.assertRaisesRegex(ValueError, 'same producer run'):
            experiment.verify_origins(origin(1), origin(1), origin(2))

    def test_corrupt_image_is_refused_even_with_valid_aot(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict('os.environ', {
                'GITHUB_REPOSITORY': 'fixture/repo', 'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '1'}):
            root = Path(temporary)
            image = root / 'image'
            image.mkdir()
            (image / 'disk.zip').write_bytes(b'original')
            experiment.artifacts.record(image, 'debian-phosh', 'inputs')
            (image / 'disk.zip').write_bytes(b'changed')
            with mock.patch.object(experiment.linux_node, 'verify', return_value={'release': {}}):
                with self.assertRaisesRegex(ValueError, 'contents do not match'):
                    experiment.validate(root, 'inputs')


if __name__ == '__main__':
    unittest.main()
