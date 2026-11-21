#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Publish a verified producer bundle under its own CI distribution identity.

Replay commit IDs in the real Linux banner vary without changing the pinned
source tree. A CI bundle must not impersonate an upstream accepted release.
Only distribution labels change here; ELF, banner, source, ABI and inventory
remain intact. Both the input and staged copy pass the full tracked verifier.
"""
from __future__ import annotations

__import__("sys").dont_write_bytecode = True

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "kernel/common/artifacts"))
from kernel_artifact import RELEASE_ID_RE, verify_artifact

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input-root", type=Path, required=True)
parser.add_argument("--output-root", type=Path, required=True)
parser.add_argument("--release-id", required=True)
args = parser.parse_args()
if not RELEASE_ID_RE.fullmatch(args.release_id):
    parser.error("invalid CI release ID")
inputs = list(args.input_root.resolve(strict=True).glob("*/kernel-artifact.json"))
if len(inputs) != 1:
    parser.error("input root must contain exactly one kernel bundle")
source = inputs[0].parent
common = ROOT / "kernel/common"
metadata = ROOT / "kernel/mainline-6.18/patches/metadata.env"
abi = common / "abi/identity.env"
manifest = verify_artifact(source, common, metadata, abi)
if manifest["family"] != "mainline-6.18" or manifest["slice"] != "arm64":
    parser.error("only the ARM64 Linux product is supported")
if args.output_root.is_symlink():
    parser.error("output root must not be a symlink")
args.output_root.mkdir(parents=True, exist_ok=True)
output = args.output_root.resolve(strict=True)
name = f"mainline-6.18-arm64-4k-{args.release_id}"
destination = output / name
if destination.exists() or destination.is_symlink():
    parser.error("CI kernel destination already exists")
with tempfile.TemporaryDirectory(prefix=".ci-kernel-", dir=output) as scratch:
    staged = Path(scratch) / name
    shutil.copytree(source, staged)
    manifest["release_id"] = args.release_id
    manifest["directory_name"] = name
    manifest["published_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    (staged / "kernel-artifact.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verify_artifact(staged, common, metadata, abi)
    os.replace(staged, destination)
print(name)
