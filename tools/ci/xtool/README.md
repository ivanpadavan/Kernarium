# Linux iOS build experiment

The Debian IPA workflow uses xtool's Xcode-project reader on ARM64 Linux to
compile the device application. CMake owns its runtime and AOT framework.
The producer consumes the accepted kernel, boot base and matching verified
private dependency prefixes; it does not rebuild third-party Apple libraries.
A missing or stale dependency prefix stops the build.

The SDK input cache contains SDKs and compiler resources copied from Xcode.
A Mac populates an absent cache without compiling the application. Linux
installs the pinned xtool tools from `archives.tsv` through dependency
preparation, then constructs its SDK offline. SDK contents and packaged
outputs carry verified provenance and content hashes.

`apple-tools.py` adapts SDK lookup and LLVM tools for the existing CMake
producer and stages arm64 XCFrameworks with their original debug symbols.
It supports packaging alone under the `xcodebuild` command name. Application
compilation uses xtool directly.

Linux omits only the application's empty asset-catalog template. Artwork or
defined asset colors require an asset compiler and stop this experiment.
Simulator test-host compilation and execution remain on an Apple silicon Mac.
The canonical validation is the Debian IPA workflow, including the Node guest
checks with AOT enabled and disabled.
