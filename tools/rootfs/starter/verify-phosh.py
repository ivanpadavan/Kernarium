#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit the installed Phosh session before publishing a starter disk (CI)."""
from __future__ import annotations

import argparse
import configparser
import json
import shlex
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()

    def guest(*command: str) -> str:
        return subprocess.check_output(['chroot', str(root), *command], text=True).strip()

    def read(guest_path: str) -> str:
        # Resolve absolute alternatives inside the image, never against the host.
        return guest('/bin/cat', guest_path)

    def executable(command: str) -> None:
        guest('/bin/sh', '-ec', 'command -v "$1" >/dev/null', 'audit', command)

    for command in ('phosh-session', 'phoc', 'gnome-session', 'squeekboard',
                    'gsettings', 'wl-copy', 'wl-paste', 'kgx'):
        executable(command)
    keyboard = configparser.ConfigParser(interpolation=None)
    keyboard.read_string(read('/etc/systemd/user/kernarium-squeekboard.service'))
    assert keyboard['Service']['BusName'] == 'sm.puri.OSK0'
    assert 'graphical-session.target' in keyboard['Unit']['PartOf']
    shell = configparser.ConfigParser(interpolation=None)
    shell.read_string(read('/etc/systemd/user/mobi.phosh.Shell.service.d/keyboard.conf'))
    assert 'kernarium-squeekboard.service' in shell['Unit']['Wants'].split()
    keyboard_enabled = guest('runuser', '-u', 'debian', '--', 'gsettings', 'get',
                             'org.gnome.desktop.a11y.applications', 'screen-keyboard-enabled')
    assert keyboard_enabled == 'true', 'The desktop must request its on-screen keyboard automatically'
    terminal_id = 'org.gnome.Console.desktop'
    favorites = guest('runuser', '-u', 'debian', '--', 'gsettings', 'get',
                      'org.gnome.shell', 'favorite-apps')
    assert terminal_id in favorites, 'The default terminal must be available in the Phosh favorites'
    terminal = configparser.ConfigParser(interpolation=None, strict=False)
    terminal.read_string(read('/usr/share/applications/' + terminal_id))
    executable(shlex.split(terminal['Desktop Entry']['Exec'])[0])
    session = configparser.ConfigParser(interpolation=None)
    session.read_string(read('/usr/share/gnome-session/sessions/phosh.session'))
    components = session['GNOME Session']['RequiredComponents'].strip(';').split(';')
    assert components and 'mobi.phosh.Shell' in components and 'sm.puri.OSK0' in components
    entries = {}
    for component in components:
        candidates = [p for directory in ('etc/xdg/autostart', 'usr/share/applications',
                                         'usr/share/gnome/autostart')
                      for p in (root / directory).glob(f'{component}.desktop')]
        assert candidates, f'No desktop entry for required component {component}'
        entry_path = '/' + str(candidates[0].relative_to(root))
        desktop = configparser.ConfigParser(interpolation=None, strict=False)
        desktop.read_string(read(entry_path))
        entry = desktop['Desktop Entry']
        assert entry.get('Hidden', 'false').lower() != 'true', entry_path
        command = shlex.split(entry['Exec'])[0]
        executable(command)
        if entry.get('TryExec'):
            executable(entry['TryExec'])
        entries[component] = {'desktop': entry_path, 'exec': entry['Exec']}
    shadow = next(line.split(':') for line in read('/etc/shadow').splitlines()
                  if line.startswith('debian:'))
    assert shadow[1] == '', 'Initial local account must be unlocked with no shared password'
    for unit in ('serial-getty@ttyS0', 'serial-getty@hvc0', 'getty@tty1'):
        text = read(f'/etc/systemd/system/{unit}.service.d/autologin.conf')
        assert '--autologin debian' in text and '%I' in text
    ssh = read('/etc/ssh/sshd_config.d/10-kernarium.conf')
    for policy in ('PasswordAuthentication no', 'KbdInteractiveAuthentication no',
                   'PermitEmptyPasswords no'):
        assert policy in ssh
    conflict = read('/etc/systemd/system/greetd.service.d/console.conf')
    assert 'Conflicts=getty@tty1.service' in conflict
    assert 'gnome-session' in read('/usr/bin/phosh-session')
    report = {'required_components': entries, 'local_account': 'debian, unlocked, no initial password',
              'console_autologin': ['ttyS0', 'hvc0', 'tty1'], 'ssh': 'owner keys only',
              'default_terminal': terminal_id, 'automatic_keyboard_requests': True,
              'boot_tests': 'pending'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
