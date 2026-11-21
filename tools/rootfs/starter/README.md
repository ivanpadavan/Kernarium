# Mainline Linux starter images

This directory owns the producer for writable mainline Linux starter disks
and for the guest boot base archives. The default products are two base
archives and three raw GPT disks:

- `uml-boot-base-aarch64.cpio.gz` and `uml-boot-base-x86_64.cpio.gz` are the
  static BusyBox and e2fsprogs base every Linux guest boots through; the
  [boot ramdisk contract](../boot-ramdisk/README.md) owns their layout, the
  per-run `/init` appended to them and their acceptance;
- `alpine-3.24.2-minimal.img` is an Alpine OpenRC root and
  `debian-13-minimal.img` a Debian systemd root, each with an automatically
  logged-in root shell on `hvc0`; and
- `debian-13-plasma-wayland.img` starts Plasma on Wayland as `debian` with
  passwordless sudo, and carries Chromium, FFmpeg, a PipeWire audio server and
  the [Virtio video VA-API driver](../../video/va-v4l2/README.md) with the
  `/etc/chromium.d` file that makes Chromium use it.

`--product debian-13-phosh` produces `debian-13-phosh.img`, an opt-in
Debian 13 ARM64 disk with Phosh, Phoc, greetd and a terminal. greetd is wanted
only by `graphical.target`; a `systemd.unit=multi-user.target` launch starts
the serial console with no desktop processes. Both modes use the same writable
root and the `debian` account with passwordless sudo. Password authentication
for SSH is disabled and host keys are generated inside the guest on its first
boot. Install your own authorized public key from the local console to use SSH.
The local account is unlocked without an initial shared password; `ttyS0`
(Kernarium), `hvc0` (Virtio-console hosts), and `tty1` auto-login as `debian`.
greetd owns `tty1` during graphical boot and conflicts with its getty. Set a
personal password with `sudo passwd debian` for the desktop lock screen.
The producer explicitly installs GNOME session/settings components and
Squeekboard and audits every required session desktop entry and executable.

The minimal Debian product also auto-logs in on these three consoles, as root,
with no initial password. It contains no Phosh or SSH server. Set a personal
password with `passwd root` from the local console. The image-only CI workflow
builds Phosh and minimal independently from a verified, cached Debian package
base without rebuilding the kernel, IPA or boot base. Integrity-checked disks
are available as draft assets during guest tests; publication requires real
boot, login, persistence and shutdown checks with a prebuilt validation host.

The guest session service accepts explicit clipboard transfers and serial TTY
geometry over host-only virtio-vsock. Clipboard transfers do not send console
input. The Phosh image starts Squeekboard with its graphical user session and
offers GNOME Console as its default terminal.

`debian-13-minimal-armhf.img` is the opt-in 32-bit Arm twin of the minimal
Debian image for AArch32 guests (see below).

Every disk has one Linux-filesystem GPT partition ending at the last usable
LBA. Networking, the account, the getty and the desktop policy belong to the
image, so launchers must not inject replacement setup scripts. The base
archives are compressed cpio initramfs parts, never another UBD device or a
legacy `/dev/ram0` block ramdisk; only the persistent GPT disk is attached as
`ubd0`. Their static executables keep the 16 KiB ELF segment alignment the
accepted bases are built with, which is valid on the 4 KiB guest.

A starter disk boots through the generic per-run `/init` with
[`boot.env`](boot.env): its sole ext4 partition becomes the read-write root.
`/init` grows that partition first, conservatively, only when a host tool has
enlarged it, and a growth failure is only a warning (the mechanics are in
[`../boot-ramdisk/init`](../boot-ramdisk/init)).

## Building

The producer runs natively on arm64 Linux and requires `sudo`, Buildroot's
ordinary build prerequisites, `debootstrap`, `e2fsprogs`, GPT fdisk, and
`qemu-user` for the armhf image. It downloads pinned Buildroot and Alpine
inputs and current Debian 13 packages. A container host is sufficient only when
its root can create character device nodes and mount `proc` and `sysfs` inside
the work tree; there is no fakeroot path. The canonical entry point is:

```sh
tools/rootfs/starter/build-all.sh \
  --work /path/to/work --output /path/to/release
```

`--product NAME` (repeatable) builds only the named products.
`release-manifest.json` and the adjacent package inventories record the exact
inputs and results. Build trees and release artifacts stay outside the source
tree.

Disk capacity is host policy. `grow-image.py` transactionally enlarges a copy
and its sole GPT partition and never changes the filesystem; the next boot
through `/init` grows ext4 before mounting it. Boot a disk with
`uml-darwin --guest-file=/boot.env=tools/rootfs/starter/boot.env ubd0=<disk>`
and attach the host Virtio-console service to reach the image-owned `hvc0`
getty.

The KDE convenience launcher, `tools/boot_kde_vnc.py`, boots the Plasma disk
of its default release from `.build/rootfs/<release>/`. It attaches the disk
read-only, so the host file must not be writable, and has the per-run
initramfs start the image's own `/sbin/init`: the network, account, getty and
Plasma session are the image's, and the launcher adds no setup. Its `--help`
owns the explicit legacy-image options; a legacy ext4 derivative of the unified
EROFS image keeps that image's [seed](../boot-ramdisk/README.md#harness-session).

## The armhf image

`debian-13-minimal-armhf.img` is built only on request
(`--product debian-13-minimal-armhf`), into a release of its own; the default
release does not hold it. It installs the same package list through
`debootstrap` alone, so no ARM program needs the network. The first stage runs
natively; the second stage, which runs the packages' maintainer scripts, is the
only ARM code the build runs, and [`armhf-run.sh`](armhf-run.sh) runs it under
Debian's `qemu-user` with no network and without the host's `binfmt_misc`.

Inputs are pinned by the Debian archive's own chain, checked by
[`debootstrap-pins.py`](debootstrap-pins.py), and the release manifest and
`debian-13-minimal-armhf.img.packages.tsv` record them.
[`audit-elf.py`](audit-elf.py) fails the build on any ELF file the 4 KiB guest
page cannot map, and [`armhf-verify.sh`](armhf-verify.sh) runs the image's core
programs under `qemu-arm`.
