#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
case "$1" in
device) preset=kernarium-ios-device ;;
simulator) preset=kernarium-ios-simulator ;;
*) echo 'Expected device or simulator' >&2; exit 2 ;;
esac
cmake --preset "$preset" \
    -DUML_LINUX_NODE_AOT_ARTIFACT="${KERNARIUM_NODE_AOT_ARTIFACT:-}" \
    -DUML_MAINLINE_618_KERNEL_ARTIFACT_DIR="$UML_MAINLINE_618_KERNEL_ARTIFACT_DIR"
cmake --build --preset "$preset" --parallel 3
Kernarium/scripts/package-ios-frameworks.sh --sdk "$1" --runtimes linux
