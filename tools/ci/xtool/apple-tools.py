#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expose LLVM's Darwin tools and SDK lookup to the existing CMake producer.

This adapter implements SDK discovery and XCFramework packaging only. App
compilation belongs to xtool; it does not emulate xcodebuild's build commands.
"""

import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys


def bundle():
    return Path(os.environ['KERNARIUM_XTOOL_SDK'])


def sdk_path(name):
    platform = {'iphoneos': 'iPhoneOS', 'iphonesimulator': 'iPhoneSimulator',
                'macosx': 'MacOSX'}[name]
    root = bundle() / f'Xcode.app/Contents/Developer/Platforms/{platform}.platform/Developer/SDKs'
    candidates = [p for p in root.glob(platform + '*.sdk') if not p.is_symlink()]
    if len(candidates) != 1:
        raise ValueError(f'Expected one {platform} SDK, found {candidates}')
    return candidates[0]


def llvm_tool(name):
    aliases = {'ar': 'ar', 'ranlib': 'ranlib', 'nm': 'nm', 'otool': 'otool',
               'lipo': 'lipo', 'strip': 'strip', 'install_name_tool': 'install-name-tool',
               'dwarfdump': 'dwarfdump'}
    if name == 'dsymutil':
        return str(bundle() / 'toolset/bin/dsymutil')
    if name == 'libtool':
        return str(bundle() / 'toolset/bin/llvm-lib')
    result = shutil.which('llvm-' + aliases[name] + '-18')
    if not result:
        raise ValueError(f'Missing LLVM tool: {name}')
    return result


def execute(command):
    os.execv(command[0], command)


def clang(name, arguments):
    compiler = Path(os.environ['KERNARIUM_SWIFT_BIN']) / name
    if arguments == ['--version'] or arguments == ['-v']:
        execute([str(compiler), *arguments])
    target = 'arm64-apple-ios26.0'
    for argument in arguments:
        if 'iPhoneSimulator' in argument:
            target += '-simulator'
            break
    flags = ['--target=' + target]
    if not any(arg in arguments for arg in ('-c', '-E', '-S')):
        flags += ['-fuse-ld=' + str(bundle() / 'toolset/bin/ld64.lld')]
        resources = bundle() / 'Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/clang'
        versions = sorted(p for p in resources.iterdir() if p.is_dir())
        if not versions:
            raise ValueError('Apple compiler runtime resources are missing')
        flags += ['-resource-dir', str(versions[-1])]
    execute([str(compiler), *flags, *arguments])


def create_xcframework(arguments):
    slices = []
    output = None
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if index + 1 == len(arguments):
            raise ValueError(f'Missing value for {option}')
        value = Path(arguments[index + 1])
        index += 2
        if option in ('-framework', '-library'):
            slices.append({'kind': option, 'path': value})
        elif option in ('-headers', '-debug-symbols') and slices:
            slices[-1][option] = value
        elif option == '-output':
            output = value
        else:
            raise ValueError(f'Unsupported XCFramework option: {option}')
    if not slices or output is None or output.exists():
        raise ValueError('XCFramework needs slices and an absent output directory')
    entries = []
    for item in slices:
        source = item['path']
        binary = source / source.stem if item['kind'] == '-framework' else source
        architectures = subprocess.check_output([llvm_tool('lipo'), '-archs', str(binary)],
                                                 text=True, timeout=60).strip()
        if architectures != 'arm64':
            raise ValueError(f'Expected arm64: {binary}')
        commands = subprocess.check_output([llvm_tool('otool'), '-l', str(binary)],
                                            text=True, timeout=60)
        platforms = set(re.findall(r'^\s*platform\s+(\S+)', commands, re.MULTILINE))
        if platforms in ({'2'}, {'IOS'}):
            identifier = 'ios-arm64'
        elif platforms in ({'7'}, {'IOSSIMULATOR'}):
            identifier = 'ios-arm64-simulator'
        else:
            raise ValueError(f'Unsupported or mixed Mach-O platforms: {platforms}')
        if any(entry['LibraryIdentifier'] == identifier for entry in entries):
            raise ValueError(f'Duplicate slice: {identifier}')
        destination = output / identifier
        destination.mkdir(parents=True)
        if source.is_dir():
            shutil.copytree(source, destination / source.name, symlinks=True)
        else:
            shutil.copy2(source, destination / source.name)
        entry = dict(LibraryIdentifier=identifier, LibraryPath=source.name,
                     SupportedArchitectures=['arm64'], SupportedPlatform='ios')
        if identifier.endswith('-simulator'):
            entry['SupportedPlatformVariant'] = 'simulator'
        if '-headers' in item:
            shutil.copytree(item['-headers'], destination / 'Headers')
            entry['HeadersPath'] = 'Headers'
        if '-debug-symbols' in item:
            symbols = item['-debug-symbols']
            shutil.copytree(symbols, destination / 'dSYMs' / symbols.name, symlinks=True)
            entry['DebugSymbolsPath'] = 'dSYMs'
        entries.append(entry)
    with (output / 'Info.plist').open('wb') as stream:
        plistlib.dump(dict(AvailableLibraries=entries, CFBundlePackageType='XFWK',
                          XCFrameworkFormatVersion='1.0'), stream)


def main(name, arguments):
    if name in ('clang', 'clang++'):
        clang(name, arguments)
    elif name == 'xcodebuild' and arguments[:1] == ['-create-xcframework']:
        create_xcframework(arguments[1:])
    elif name == 'xcrun':
        sdk = 'iphoneos'
        if arguments[:1] in (['--sdk'], ['-sdk']):
            sdk, arguments = arguments[1], arguments[2:]
        if arguments == ['--show-sdk-path']:
            print(sdk_path(sdk))
        elif arguments in (['--show-sdk-version'], ['--show-sdk-build-version']):
            settings = plistlib.loads((sdk_path(sdk) / 'SDKSettings.plist').read_bytes())
            key = 'Version' if arguments[0] == '--show-sdk-version' else 'ProductBuildVersion'
            print(settings[key])
        elif arguments[:1] in (['--find'], ['-f']):
            tool = arguments[1]
            print(Path(__file__).absolute().parent / tool if tool in ('clang', 'clang++') else llvm_tool(tool))
        elif arguments:
            tool = arguments[0]
            if tool in ('clang', 'clang++'):
                clang(tool, ['-isysroot', str(sdk_path(sdk)), *arguments[1:]])
            else:
                execute([llvm_tool(tool), *arguments[1:]])
        else:
            raise ValueError('xcrun requires an SDK query or tool')
    elif name in ('ar', 'ranlib', 'nm', 'otool', 'lipo', 'strip', 'install_name_tool', 'dwarfdump', 'dsymutil', 'libtool'):
        execute([llvm_tool(name), *arguments])
    else:
        raise ValueError(f'Unsupported tool invocation: {name} {arguments}')


if __name__ == '__main__':
    try:
        main(Path(sys.argv[0]).name, sys.argv[1:])
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'Kernarium xtool adapter: {error}')
