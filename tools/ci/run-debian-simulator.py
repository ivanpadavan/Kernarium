#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run prebuilt Simulator tests, retaining real test counts and boot diagnostics."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time


def run(*command, timeout=120, **kwargs):
    return subprocess.run(command, check=True, timeout=timeout, stdin=subprocess.DEVNULL, **kwargs)


def capture(*command):
    # A service spawned by an Apple command may inherit its output handle.
    # A regular file does not make command completion wait for pipe EOF.
    with tempfile.TemporaryFile() as output:
        try:
            run(*command, stdout=output, timeout=60)
        except subprocess.SubprocessError:
            output.seek(0)
            print('Command output before failure:', output.read().decode('utf-8', errors='replace'), flush=True)
            raise
        output.seek(0)
        return output.read().decode('utf-8').strip()


def create_simulator(family):
    def listing(kind):
        return json.loads(capture('xcrun', 'simctl', 'list', kind, '--json'))[kind]
    deadline = time.monotonic() + 180
    runtimes = [r for r in listing('runtimes') if r['isAvailable'] and '.iOS-' in r['identifier'] and int(r['version'].split('.')[0]) >= 27]
    models = sorted(listing('devicetypes'), key=lambda m: (m.get('minRuntimeVersion', 0), m['name']), reverse=True)
    for runtime in sorted(runtimes, key=lambda r: tuple(map(int, r['version'].split('.'))), reverse=True):
        supported = runtime.get('supportedDeviceTypes')
        allowed = {m['identifier'] for m in supported} if supported is not None else None
        parts = list(map(int, runtime['version'].split('.'))) + [0, 0]
        version = parts[0] << 16 | parts[1] << 8 | parts[2]
        for model in models:
            if not model['name'].startswith(family):
                continue
            if allowed is not None and model['identifier'] not in allowed:
                continue
            if not model.get('minRuntimeVersion', 0) <= version <= model.get('maxRuntimeVersion', 0xffffffff):
                continue
            if time.monotonic() > deadline:
                raise RuntimeError('Simulator selection exceeded its deadline')
            result = subprocess.run(['xcrun', 'simctl', 'create', 'Debian validation', model['identifier'], runtime['identifier']], text=True, capture_output=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.strip()
    raise RuntimeError('No installed compatible iOS 27 Simulator')


def patch_targets(value, environment):
    if isinstance(value, dict):
        value.pop('OnlyTestIdentifiers', None)
        value.pop('SkipTestIdentifiers', None)
        if 'TestBundlePath' in value:
            value.setdefault('EnvironmentVariables', {}).update(environment)
            value.setdefault('TestingEnvironmentVariables', {}).update(environment)
        for child in list(value.values()):
            patch_targets(child, environment)
    elif isinstance(value, list):
        for child in value:
            patch_targets(child, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--products', type=Path, required=True)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--kind', default='phosh')
    parser.add_argument('--suite', default='DebianImageTests')
    parser.add_argument('--features', action='store_true')
    parser.add_argument('--storage', action='store_true')
    parser.add_argument('--single-phase', action='store_true')
    parser.add_argument('--family', choices=['iPad', 'iPhone'], default='iPad')
    parser.add_argument('--ui-case', help='A concrete KernariumUITests class/method to execute')
    parser.add_argument('--state', type=Path, help='Reuse one Simulator across app test suites')
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.cleanup:
        if args.state and args.state.exists():
            udid = json.loads(args.state.read_text())['device']
            subprocess.run(['xcrun', 'simctl', 'shutdown', udid], timeout=60)
            subprocess.run(['xcrun', 'simctl', 'delete', udid], check=True, timeout=60)
            args.state.unlink()
        return
    products = args.products.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reused = bool(args.state and args.state.exists())
    udid = json.loads(args.state.read_text())['device'] if reused else create_simulator(args.family)
    if args.state and not reused:
        args.state.write_text(json.dumps({'device': udid}) + '\n')
    container = None
    failed = False
    try:
        if not reused:
            run('xcrun', 'simctl', 'boot', udid)
        run('xcrun', 'simctl', 'bootstatus', udid, '-b', timeout=240)
        app = products / 'Release-iphonesimulator/Kernarium.app'
        run('xcrun', 'simctl', 'install', udid, str(app), timeout=300)
        container = Path(capture('xcrun', 'simctl', 'get_app_container', udid, 'com.imxieyi.Kernarium', 'data'))
        documents = container / 'Documents'
        documents.mkdir(exist_ok=True)
        if args.image:
            destination = documents / args.image.name
            if destination.exists():
                destination.unlink()
            run('cp', '-c', str(args.image.resolve()), str(documents / args.image.name), timeout=300)
        originals = list(products.glob('*.xctestrun'))
        assert len(originals) == 1, originals
        original = plistlib.loads(originals[0].read_bytes())
        phases = ('write', 'grow', 'read') if args.storage else (('write', 'read') if args.image else ('unit',))
        if args.single_phase:
            phases = phases[:1]
        for phase in phases:
            if args.storage and phase == 'grow':
                image = documents / args.image.name
                before = image.stat()
                target = ((before.st_size // (1024**3)) + 2) * (1024**3)
            environment = {
                'KERNARIUM_ROOTFS': args.image.name if args.image else 'unit-tests-no-guest.img',
                'KERNARIUM_LINUX_CONSOLE_BOOT': '0' if phase == 'write' and args.kind == 'phosh' and not args.storage else '1',
                'KERNARIUM_IMAGE_SMOKE': '1' if args.suite == 'DebianImageTests' else '0',
                'KERNARIUM_IMAGE_KIND': args.kind,
                'KERNARIUM_IMAGE_PHASE': phase,
                'KERNARIUM_IMAGE_FEATURES': '1' if args.features else '0',
                'KERNARIUM_DEBIAN_SMOKE': phase if args.suite == 'PersistentDebianTests' else '',
                'KERNARIUM_MOBILE_SMOKE': '1' if args.suite == 'DebianMobileTests' else '0',
                'KERNARIUM_STORAGE_SMOKE': '1' if args.storage else '0',
                'KERNARIUM_STORAGE_PHASE': phase,
            }
            configuration = plistlib.loads(plistlib.dumps(original))
            patch_targets(configuration, environment)
            selected = products / 'selected.xctestrun'
            selected.write_bytes(plistlib.dumps(configuration))
            result = output / (phase + '.xcresult')
            test_identifier = 'KernariumUITests/' + args.ui_case if args.ui_case else 'KernariumTests/' + args.suite
            command = ['xcodebuild', 'test-without-building', '-xctestrun', str(selected), '-destination', 'id=' + udid,
                       '-parallel-testing-enabled', 'NO', '-resultBundlePath', str(result),
                       '-only-testing:' + test_identifier, 'CODE_SIGNING_ALLOWED=NO']
            log_path = output / (phase + '-boot.log')
            with log_path.open('wb') as log, log_path.open('rb') as live:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           env={**os.environ, **{'TEST_RUNNER_' + k: v for k, v in environment.items()}})
                deadline = time.monotonic() + 1200
                try:
                    while True:
                        chunk = live.read()
                        if chunk:
                            print(chunk.decode('utf-8', errors='replace'), end='', flush=True)
                        if process.poll() is not None:
                            # Re-read after observing exit, so the final test
                            # output is retained even if a service holds stdout.
                            print(live.read().decode('utf-8', errors='replace'), end='', flush=True)
                            break
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command, 1200)
                        time.sleep(0.2)
                except BaseException:
                    process.kill()
                    process.wait(timeout=30)
                    raise
            summary = json.loads(capture('xcrun', 'xcresulttool', 'get', 'test-results', 'summary', '--path', str(result)))
            (output / (phase + '-summary.json')).write_text(json.dumps(summary, indent=2) + '\n')
            counts = {k: summary.get(k) for k in ('totalTestCount', 'passedTests', 'failedTests', 'skippedTests')}
            print('ACTUAL_TEST_COUNTS:', phase, counts, flush=True)
            assert process.returncode == 0, process.returncode
            assert counts['totalTestCount'] > 0 and counts['passedTests'] == counts['totalTestCount'], counts
            assert counts['failedTests'] == 0 and counts['skippedTests'] == 0, counts
            if args.image:
                assert counts['totalTestCount'] == 1, counts
            elif args.ui_case:
                assert counts['totalTestCount'] == 1, counts
            elif args.suite == 'DebianAppTests':
                assert counts['totalTestCount'] == 5, counts
            elif args.suite == 'GuestJITTests':
                assert counts['totalTestCount'] == 3, counts
            if args.storage and phase == 'grow':
                after = (documents / args.image.name).stat()
                assert after.st_size == target and after.st_size > before.st_size
                assert after.st_blocks * 512 < after.st_size, 'Growth must preserve a sparse image'
                print('STORAGE_HOST_PROOF:', before.st_size, '->', after.st_size,
                      'allocated bytes:', after.st_blocks * 512, flush=True)
    except BaseException as error:
        failed = True
        (output / 'runner-error.txt').write_text(repr(error) + '\n')
        raise
    finally:
        try:
            if container is not None:
                log = container / 'Library/Caches/debian-boot.log'
                if log.exists():
                    shutil.copyfile(log, output / 'application-boot.log')
            if failed:
                with (output / 'simulator-host.log').open('w') as log:
                    subprocess.run(['log', 'show', '--last', '3m', '--style', 'compact', '--predicate',
                                    'subsystem CONTAINS "CoreSimulator" OR process == "installd"'],
                                   stdout=log, stderr=subprocess.STDOUT, timeout=45)
            subprocess.run(['xcrun', 'simctl', 'io', udid, 'screenshot', str(output / 'screen.png')], timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            print('Diagnostic collection:', error, flush=True)
        finally:
            if not args.state:
                subprocess.run(['xcrun', 'simctl', 'shutdown', udid], timeout=60)
                subprocess.run(['xcrun', 'simctl', 'delete', udid], timeout=60)


if __name__ == '__main__':
    main()
