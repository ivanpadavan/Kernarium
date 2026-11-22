#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Display requests validate mandatory geometry and never apply host UI scale."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('guest_display', ROOT / 'tools/rootfs/starter/products/debian-phosh/kernarium-display-session.py')
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class Desktop:
    def __init__(self):
        self.size = (720, 1440)
        self.calls = []
        self.apply = True

    def __call__(self, arguments):
        self.calls.append(arguments)
        if arguments:
            if self.apply:
                self.size = tuple(map(int, arguments[-1].split('x')))
            return ''
        return (f'Virtual-1 "Virtual monitor"\n  Enabled: yes\n  Modes:\n'
                f'    {self.size[0]}x{self.size[1]} px, 60.000000 Hz (current)\n'
                '    1280x720 px, 60.000000 Hz\n  Scale: 2.000000\n')


class DisplayRequests(unittest.TestCase):
    def test_only_width_and_height_are_required(self):
        desktop = Desktop()
        reply = display.configure_display(dict(version=1, width=1280, height=720), desktop)
        self.assertEqual((reply['width'], reply['height']), (1280, 720))
        self.assertIn(['--output', 'Virtual-1', '--mode', '1280x720'], desktop.calls)

    def test_optional_metadata_never_becomes_compositor_scale(self):
        desktop = Desktop()
        reply = display.configure_display(dict(version=1, width=1920, height=1080,
            metadata=dict(scale=3, nativeScale=3, displayName='Test receiver', future=dict(value=True))), desktop)
        self.assertEqual((reply['width'], reply['height']), (1920, 1080))
        self.assertIn(['--output', 'Virtual-1', '--custom-mode', '1920x1080'], desktop.calls)
        self.assertTrue(all('--scale' not in command for command in desktop.calls))

    def test_invalid_geometry_is_rejected_before_changing_the_display(self):
        for width, height in ((0, 720), (-1, 720), (1280, 4096), (True, 720), ('1280', 720)):
            with self.subTest(width=width, height=height):
                desktop = Desktop()
                with self.assertRaises(ValueError):
                    display.configure_display(dict(version=1, width=width, height=height), desktop)
                self.assertEqual(desktop.calls, [])

    def test_failed_readback_is_not_reported_as_success(self):
        desktop = Desktop()
        desktop.apply = False
        with self.assertRaisesRegex(RuntimeError, 'did not apply'):
            display.configure_display(dict(version=1, width=1280, height=720), desktop)


if __name__ == '__main__':
    unittest.main()
