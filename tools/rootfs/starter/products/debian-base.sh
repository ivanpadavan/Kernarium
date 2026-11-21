# SPDX-License-Identifier: GPL-2.0-or-later

# Everything an image's package set needs before its packages are installed.
prepare_debian_base()
{
	root=$1
	write_root_file "$root/etc/apt/sources.list" 0644 <<EOF
deb http://deb.debian.org/debian $DEBIAN_SUITE main
deb http://deb.debian.org/debian $DEBIAN_SUITE-updates main
deb http://security.debian.org/debian-security $DEBIAN_SUITE-security main
EOF
	write_root_file "$root/usr/sbin/policy-rc.d" 0755 <<'EOF'
#!/bin/sh
exit 101
EOF
}

install_debian_base()
{
	root=$1
	as_root cp -L /etc/resolv.conf "$root/etc/resolv.conf"
	as_root chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
		apt-get update
	# systemd loads libbpf at runtime for its cgroup BPF features and reports
	# them disabled at every boot when the library is absent.
	as_root chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
		apt-get install -y --no-install-recommends \
		systemd-sysv systemd-resolved udev kmod dbus ca-certificates \
		libbpf1 python3 tmux procps
}

# The image's own policy, written and enabled from the host: it executes
# nothing from the root, so it serves every architecture alike.
configure_debian_base()
{
	root=$1
	install_guest_session "$root"
	hostname=$2
	autologin_user=$3
	write_root_file "$root/etc/hostname" 0644 <<EOF
$hostname
EOF
	write_root_file "$root/etc/hosts" 0644 <<EOF
127.0.0.1 localhost
127.0.1.1 $hostname
::1 localhost ip6-localhost ip6-loopback
EOF
	write_root_file "$root/etc/fstab" 0644 <<'EOF'
/dev/ubda1 / ext4 defaults,noatime 0 1
EOF
	# Debian's systemd links /etc/default/locale to this file at boot, and
	# pam_env reads it for every login. C.UTF-8 needs no locales package.
	write_root_file "$root/etc/locale.conf" 0644 <<'EOF'
LANG=C.UTF-8
EOF
	# Match wired links by type rather than by kernel-assigned name, and keep
	# the image's own hostname: the DHCP-supplied one could only be applied
	# through hostnamed, which refuses networkd without polkit.
	write_root_file "$root/etc/systemd/network/20-wired.network" 0644 <<'EOF'
[Match]
Type=ether
Kind=!*

[Network]
DHCP=yes
IPv6AcceptRA=yes

[DHCPv4]
UseDNS=yes
UseHostname=no

[DHCPv6]
UseHostname=no
EOF
	write_root_file \
		"$root/etc/systemd/system/serial-getty@hvc0.service.d/autologin.conf" \
		0644 <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $autologin_user --noclear - \$TERM
EOF
	as_root systemctl --root="$root" enable \
		systemd-networkd.service systemd-resolved.service \
		serial-getty@hvc0.service
	as_root ln -sfn /run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
}

install_guest_session()
{
	root=$1
	as_root mkdir -p "$root/usr/libexec" "$root/usr/lib/systemd/system"
	as_root install -m 0755 "$SCRIPT_DIR/guest/kernarium-session.py" "$root/usr/libexec/kernarium-session"
	as_root install -m 0644 "$SCRIPT_DIR/guest/kernarium-session.service" "$root/usr/lib/systemd/system/kernarium-session.service"
	as_root systemctl --root="$root" enable kernarium-session.service
}

prepare_debian_root()
{
	root=$1
	if [ -n "$debian_base_archive" ]; then
		python3 -B "$SCRIPT_DIR/verify-debian-base.py" --archive "$debian_base_archive"
		reset_root_directory "$root"
		as_root tar --numeric-owner -C "$root" -xzf "$debian_base_archive"
		mount_chroot_proc "$root"
		as_root rm -f "$root/etc/resolv.conf"
		as_root cp -L /etc/resolv.conf "$root/etc/resolv.conf"
		as_root chroot "$root" apt-get update
	else
		debootstrap_root "$root"
		mount_chroot_proc "$root"
		prepare_debian_base "$root"
		install_debian_base "$root"
	fi
}

build_debian_base()
{
	root=$roots_dir/debian-base
	debootstrap_root "$root"
	mount_chroot_proc "$root"
	prepare_debian_base "$root"
	install_debian_base "$root"
	unmount_chroot_proc
	cat "$root/etc/debian_version" > "$work_dir/debian.version"
	producer_reset_machine_id "$root"
	as_root rm -f "$root/etc/resolv.conf"
	as_root ln -s /run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
	as_root chroot "$root" dpkg-query -W -f='${binary:Package}\t${Version}\n' | sort > "$output_dir/debian-13-base.packages.tsv"
	destination=$(artifact_path debian-13-base.tar.gz)
	as_root tar --numeric-owner --sparse -C "$root" -czf "$destination" .
	as_root chown "$owner_uid:$owner_gid" "$destination"
}

prune_debian()
{
	root=$1
	as_root rm -f -- "$root/usr/sbin/policy-rc.d"
	as_root rm -rf -- "$root/var/lib/apt/lists"/* "$root/var/cache/apt"/* \
		"$root/tmp"/* "$root/var/tmp"/* "$root/run"/*
	as_root find "$root/var/log" -type f -exec truncate -s 0 {} \;
	as_root find "$root/usr/share/doc" -type f ! -name copyright -delete
	as_root find "$root/usr/share/doc" -type l -delete
	as_root find "$root/usr/share/doc" -depth -type d -empty -delete
	as_root rm -rf -- "$root/usr/share/man"/* "$root/usr/share/info"/* \
		"$root/usr/share/lintian" "$root/usr/share/doc-base"
	as_root find "$root/usr/share/locale" -mindepth 1 -maxdepth 1 \
		! -name 'en' ! -name 'en_*' -exec rm -rf -- {} +
	producer_reset_machine_id "$root"
}

debootstrap_root()
{
	root=$1
	reset_root_directory "$root"
	as_root debootstrap --arch=arm64 --variant=minbase --components=main \
		"$DEBIAN_SUITE" "$root" http://deb.debian.org/debian
}


# Kernarium's fd-backed UML console is ttyS0; CLI hosts may expose hvc0.
# tty1 remains usable in console mode. A display manager owns it in GUI mode.
configure_debian_local_console()
{
	root=$1
	user=$2
	for unit in serial-getty@ttyS0 serial-getty@hvc0 getty@tty1; do
		write_root_file "$root/etc/systemd/system/$unit.service.d/autologin.conf" 0644 <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $user --noclear %I \$TERM
EOF
		as_root systemctl --root="$root" enable "$unit.service"
	done
}
