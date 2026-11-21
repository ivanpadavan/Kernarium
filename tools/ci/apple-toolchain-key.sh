#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Fingerprint real Apple SDK/compiler and build tools for compatible CI caches.
set -eu
record=$(mktemp)
trap 'rm -f "$record"' EXIT HUP INT TERM
{
	uname -m
	sw_vers -productVersion
	sw_vers -buildVersion
	xcodebuild -version
	for sdk in macosx iphoneos iphonesimulator; do
		xcrun --sdk "$sdk" --show-sdk-version
		xcrun --sdk "$sdk" --show-sdk-build-version
	done
	xcrun --sdk iphoneos clang --version
	clang --version
	for tool in cmake ninja meson gmake autoconf automake glibtool \
		pkg-config bison flex gperf; do
		command -v "$tool"
		"$tool" --version
	done
	python3 --version
	if [ -x .build/gpu-tools-venv/bin/python ]; then
		.build/gpu-tools-venv/bin/python -m pip freeze
	fi
} > "$record"
shasum -a 256 "$record" | awk '{print $1}'
