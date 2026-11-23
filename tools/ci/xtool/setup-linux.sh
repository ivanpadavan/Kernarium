#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -euo pipefail

ROOT=$(pwd)
TOOLS="$ROOT/.build/xtool"
dependencies/prepare.sh --archive-manifest tools/ci/xtool/archives.tsv
mkdir -p "$TOOLS/bin"
archive() {
    python3 - "$ROOT" "$1" <<'PY'
import csv, pathlib, sys
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
with (root/'tools/ci/xtool/archives.tsv').open() as stream:
    rows = csv.DictReader((line for line in stream if not line.startswith('#')), delimiter='\t')
    row = next(row for row in rows if row['name'] == name)
path = root/'.build/dependency-cache/archives'/(row['sha256']+'-'+name)
if not path.is_file():
    raise SystemExit('Verified archive is absent: '+name)
print(path)
PY
}
cp "$(archive xtool-aarch64.AppImage)" "$TOOLS/xtool.AppImage"
chmod +x "$TOOLS/xtool.AppImage"
(cd "$TOOLS" && ./xtool.AppImage --appimage-extract >/dev/null)
ln -s "$TOOLS/squashfs-root/AppRun" "$TOOLS/bin/xtool"
export XTL_TOOLSET_TARBALL
export XTL_OAM_TARBALL
XTL_TOOLSET_TARBALL=$(archive toolset-aarch64.tar.gz)
XTL_OAM_TARBALL=$(archive OpenAppleMacrosServer-aarch64.tar.gz)
"$TOOLS/bin/xtool" sdk build "$ROOT/.build/xtool-sdk-input/Xcode.app" "$TOOLS" --arch arm64 </dev/null
"$TOOLS/bin/xtool" sdk install "$TOOLS/darwin.xtoolsdk" </dev/null

for tool in xcrun xcodebuild clang clang++ ar ranlib nm otool lipo strip install_name_tool dwarfdump dsymutil libtool; do
    ln -s "$ROOT/tools/ci/xtool/apple-tools.py" "$TOOLS/bin/$tool"
done
chmod +x tools/ci/xtool/apple-tools.py
{
    echo "KERNARIUM_XTOOL_SDK=$TOOLS/darwin.xtoolsdk"
    echo "KERNARIUM_SWIFT_BIN=$(dirname "$(command -v swift)")"
    echo "CC=$TOOLS/bin/clang"
    echo "CXX=$TOOLS/bin/clang++"
    echo "OBJCXX=$TOOLS/bin/clang++"
} >> "$GITHUB_ENV"
echo "$TOOLS/bin" >> "$GITHUB_PATH"
"$TOOLS/bin/xtool" --version
swift --version
