#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source changes select independent products and preserve unrelated identities."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('debian_inputs', ROOT / 'tools/ci/debian-inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


class Tree(inputs.Repository):
    def __init__(self, files):
        self.data = files
        self.paths = list(files)

    def read(self, path):
        return self.data[path]


def fixture():
    driver = b'BUILDROOT_VERSION=2026.02\nBUSYBOX_VERSION=1.37\n'
    for function in ('download_checked', 'artifact_path', 'prepare_busybox_config', 'build_boot_base'):
        driver += function.encode() + b'()\n{\n true\n}\n'
    return {
        'tools/rootfs/starter/build-all.sh': driver,
        'tools/rootfs/starter/products/debian-base.sh': b'common packages',
        'tools/rootfs/starter/products/debian-phosh.sh': b'phosh packages',
        'tools/rootfs/starter/products/debian-minimal.sh': b'minimal policy',
        'Kernarium/Kernarium/ContentView.swift': b'view',
        'kernel/mainline-6.18/patches/series.tsv': b'tree\tkernel-tree\n',
        'dependencies/manifest.tsv': b'slirp\t1\tios-network\tgit\turl\trevision\tpatches/slirp/series.tsv\n',
        'dependencies/patches/slirp/series.tsv': b'tree\tsource-tree\ntopic\tone\n',
        'dependencies/build-ios-network.sh': b'network recipe',
        'dependencies/build-apple-gpu.sh': b'gpu recipe',
    }


class IndependentInputs(unittest.TestCase):
    def change(self, path, contents):
        before = fixture()
        after = {**before, path: contents}
        first, last = Tree(before).identities(), Tree(after).identities()
        return inputs.plan(first, last), first, last

    def test_phosh_packages_reuse_base_minimal_kernel_and_apple(self):
        result, first, last = self.change('tools/rootfs/starter/products/debian-phosh.sh', b'phosh packages keyboard fix')
        self.assertEqual(result['images'], ['phosh'])
        self.assertFalse(result['app'])
        for key in ('debian-base', 'minimal', 'kernel', 'boot-base', 'gpu-ios', 'gpu-ios-simulator'):
            self.assertEqual(first[key], last[key], key)

    def test_ui_change_only_selects_the_application(self):
        result, _, _ = self.change('Kernarium/Kernarium/ContentView.swift', b'new clipboard button')
        self.assertTrue(result['app'])
        self.assertEqual(result['images'], [])
        self.assertFalse(result['kernel'])
        self.assertFalse(result['boot_base'])

    def test_common_package_change_selects_both_images_without_an_ipa(self):
        result, _, _ = self.change('tools/rootfs/starter/products/debian-base.sh', b'common packages plus tmux')
        self.assertEqual(result['images'], ['phosh', 'minimal'])
        self.assertFalse(result['app'])
        self.assertFalse(result['boot_base'])

    def test_network_recipe_keeps_gpu_and_images(self):
        result, first, last = self.change('dependencies/build-ios-network.sh', b'new network recipe')
        self.assertTrue(result['app'])
        self.assertEqual(result['images'], [])
        self.assertEqual(first['gpu-ios-simulator'], last['gpu-ios-simulator'])
        self.assertNotEqual(first['network-ios-simulator'], last['network-ios-simulator'])

    def test_patch_message_or_order_without_tree_change_preserves_products(self):
        result, _, _ = self.change('dependencies/patches/slirp/series.tsv', b'tree\tsource-tree\ntopic\trenamed\n')
        self.assertFalse(result['app'])
        self.assertEqual(result['images'], [])

    def test_boot_recipe_change_selects_both_consumers(self):
        before = fixture()
        after = {**before, 'tools/rootfs/starter/build-all.sh': before['tools/rootfs/starter/build-all.sh'].replace(b'BUSYBOX_VERSION=1.37', b'BUSYBOX_VERSION=1.38')}
        result = inputs.plan(Tree(before).identities(), Tree(after).identities())
        self.assertTrue(result['boot_base'])
        self.assertTrue(result['app'])
        self.assertEqual(result['images'], ['phosh', 'minimal'])

    def test_same_source_inputs_keep_every_cache_identity(self):
        first = Tree(fixture()).identities()
        result = inputs.plan(first, Tree(dict(reversed(list(fixture().items())))).identities())
        self.assertEqual(result['changed'], [])
        self.assertEqual(result['images'], [])
        self.assertFalse(result['app'])


if __name__ == '__main__':
    unittest.main()
