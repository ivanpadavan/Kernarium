#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Accept a newly produced CI base only after the complete base audit passes."""
from __future__ import annotations

__import__("sys").dont_write_bytecode = True

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--release", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if not args.release or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.release):
    parser.error("release must contain only lowercase letters, digits and hyphens")
root = Path(__file__).resolve().parents[2]
archive = args.archive.resolve(strict=True)
if archive.name != "uml-boot-base-aarch64.cpio.gz":
    parser.error("archive must be the AArch64 boot base")
data = archive.read_bytes()
# This is a new fork release, never a replacement for an upstream acceptance.
text = (
    "# SPDX-License-Identifier: GPL-2.0-or-later\n"
    "UML_BOOT_BASE_SCHEMA=1\n"
    f"UML_BOOT_BASE_RELEASE='{args.release}'\n"
    f"UML_BOOT_BASE_FILE='{archive.name}'\n"
    "UML_BOOT_BASE_ARCHITECTURE='aarch64'\n"
    f"UML_BOOT_BASE_SIZE={len(data)}\n"
    f"UML_BOOT_BASE_SHA256='{hashlib.sha256(data).hexdigest()}'\n"
)
args.output.write_text(text)
try:
    subprocess.run([
        sys.executable, "-B", str(root / "tools/rootfs/boot-ramdisk/verify_base.py"),
        "--descriptor", str(args.output), "--archive", str(archive),
    ], check=True)
except BaseException:
    args.output.unlink(missing_ok=True)
    raise
