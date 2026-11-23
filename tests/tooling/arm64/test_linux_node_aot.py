#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux Node windows preserve instruction addresses and immutable file bytes."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools/aot'))
import elf64
import linux_node


class Image:
    data = bytes(16384)
    sha256 = 'ab' * 32

    def executable_segment(self):
        return elf64.Segment(4096, 4096, 12288, 12288, 5)

    def functions(self):
        return [elf64.Function(4096, 12288), elf64.Function(12288, 16384)]


class Windows(unittest.TestCase):
    def test_embedded_builtin_text_does_not_need_unwind_tables(self):
        image = Image()
        image.functions = lambda: []
        window = elf64.ExecutableWindow(image, 4096, 12288, True)
        self.assertEqual(window.functions(), [elf64.Function(4096, 8192),
                                              elf64.Function(8192, 12288)])

    def test_crossing_function_keeps_both_halves_at_original_addresses(self):
        first = elf64.ExecutableWindow(Image(), 4096, 8192)
        second = elf64.ExecutableWindow(Image(), 8192, 16384)
        self.assertEqual(first.functions(), [elf64.Function(4096, 8192)])
        self.assertEqual(second.functions(), [elf64.Function(8192, 12288), elf64.Function(12288, 16384)])
        self.assertEqual(second.executable_segment().offset, 8192)
        self.assertIs(first.data, Image.data)

    def test_invalid_or_outside_windows_are_refused(self):
        for begin, end in ((4097, 8192), (4096, 8193), (8192, 4096), (0, 4096), (4096, 20480)):
            with self.subTest(begin=begin, end=end), self.assertRaises(elf64.ElfError):
                elf64.ExecutableWindow(Image(), begin, end)

    def test_mismatched_capsule_is_rejected_before_installing_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / 'input'
            output.mkdir()
            (output / 'node.erofs').write_bytes(b'changed capsule')
            root = Path(scratch) / 'root'
            with mock.patch.object(linux_node, 'verify', return_value={
                    'release': {'system_partition_sha256': '00' * 32}}):
                with self.assertRaisesRegex(ValueError, 'capsule differs'):
                    linux_node.install(output, root)
            self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
