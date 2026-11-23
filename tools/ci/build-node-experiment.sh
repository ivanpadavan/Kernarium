#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -euo pipefail
OUTPUT=$1
INPUT_KEY=$2
dependencies/prepare.sh --archive-manifest tools/aot/linux-node-archives.tsv
ARCHIVE=$(find .build/dependency-cache/archives -name '*-node-v22.16.0-linux-arm64.tar.xz')
test -n "$ARCHIVE"
timeout 7200 python3 -B tools/aot/linux_node.py build --archive "$ARCHIVE" --output "$OUTPUT/node"
export KERNARIUM_NODE_AOT_INPUT="$OUTPUT/node"
tools/rootfs/starter/build-all.sh --work "$RUNNER_TEMP/node-image-work" --output "$OUTPUT/image" --product debian-13-phosh
zip -9 -j "$OUTPUT/image/debian-13-phosh.img.zip" "$OUTPUT/image/debian-13-phosh.img"
rm "$OUTPUT/image/debian-13-phosh.img"
python3 -B tools/ci/debian-artifacts.py record --root "$OUTPUT/image" --kind debian-phosh --key "$INPUT_KEY"
