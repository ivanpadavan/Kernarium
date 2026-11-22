# SPDX-License-Identifier: GPL-2.0-or-later

# The same writable root boots either target. greetd is wanted only by
# graphical.target, so multi-user.target starts no compositor or desktop.
build_debian_phosh()
{
	printf '%s\n' "==> Building Debian 13 Phosh GPT image"
	root=$roots_dir/debian-phosh
	prepare_debian_root "$root"
	as_root chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
		apt-get install -y --no-install-recommends \
		phosh phoc greetd foot dbus-user-session libpam-systemd \
		gnome-session-bin gnome-session-common gnome-settings-daemon \
		gnome-settings-daemon-common squeekboard \
		libgl1-mesa-dri libegl-mesa0 sudo openssh-server procps wl-clipboard gnome-console \
		libglib2.0-bin dconf-cli at-spi2-core polkitd librsvg2-common
	as_root test -x "$root/usr/bin/phosh-session"
	as_root test -x "$root/usr/sbin/agreety"
	as_root chroot "$root" useradd --create-home --uid 1000 --shell /bin/bash debian
	# Unlock local initial-session/account checks without publishing a password.
	as_root chroot "$root" passwd -d debian
	for group in video render input audio; do
		as_root chroot "$root" groupadd --force "$group"
		as_root chroot "$root" usermod --append --groups "$group" debian
	done
	configure_debian_base "$root" debian debian
	configure_debian_local_console "$root" debian
	configure_phosh_session "$root"
	write_root_file "$root/etc/sudoers.d/debian" 0440 <<'EOF'
debian ALL=(ALL:ALL) NOPASSWD: ALL
EOF
	# No shared password or SSH host identity is published. The local console
	# autologin is the setup path; SSH accepts keys installed by the owner.
	write_root_file "$root/etc/ssh/sshd_config.d/10-kernarium.conf" 0644 <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin prohibit-password
EOF
	as_root rm -f "$root/etc/ssh/ssh_host_"*
	write_root_file "$root/etc/systemd/system/ssh-host-keys.service" 0644 <<'EOF'
[Unit]
Description=Generate this guest's SSH host keys
Before=ssh.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key
[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
[Install]
WantedBy=multi-user.target
EOF
	write_root_file "$root/etc/greetd/config.toml" 0644 <<'EOF'
[terminal]
vt = 1
[default_session]
command = "/usr/sbin/agreety --cmd /usr/bin/phosh-session"
user = "_greetd"
[initial_session]
command = "/usr/bin/phosh-session"
user = "debian"
EOF
	write_root_file "$root/etc/systemd/system/greetd.service.d/console.conf" 0644 <<'EOF'
[Unit]
Conflicts=getty@tty1.service
After=getty@tty1.service
EOF
	# Debian's package may enable greetd through a display-manager alias.
	# Keep one explicit graphical dependency and none in multi-user.target.
	as_root systemctl --root="$root" disable greetd.service || true
	as_root rm -f "$root/etc/systemd/system/display-manager.service" \
		"$root/etc/systemd/system/graphical.target.wants/phosh.service"
	as_root mkdir -p "$root/etc/systemd/system/graphical.target.wants"
	as_root ln -s /usr/lib/systemd/system/greetd.service \
		"$root/etc/systemd/system/graphical.target.wants/greetd.service"
	as_root systemctl --root="$root" enable ssh-host-keys.service ssh.service
	as_root systemctl --root="$root" set-default graphical.target
	as_root python3 "$SCRIPT_DIR/verify-phosh.py" --root "$root" \
		--output "$output_dir/debian-13-phosh.audit.json"
	as_root chroot "$root" runuser -u debian -- /bin/sh -ec 'test "$(id -u)" = 1000; sudo -n true'
	as_root chroot "$root" dpkg-query -W -f='${binary:Package}\t${Version}\n' | \
		sort > "$work_dir/debian-phosh.packages.tsv"
	unmount_chroot_proc
	cat "$root/etc/debian_version" > "$work_dir/debian.version"
	prune_debian "$root"
	destination=$(artifact_path debian-13-phosh.img)
	as_root python3 "$SCRIPT_DIR/make-gpt-image.py" \
		--root "$root" --output "$destination" --label debian-phosh \
		--headroom-mib 1024
	as_root chown "$owner_uid:$owner_gid" "$destination"
	cp "$work_dir/debian-phosh.packages.tsv" "$destination.packages.tsv"
}

configure_phosh_session()
{
	root=$1
	write_root_file "$root/etc/systemd/user/kernarium-squeekboard.service" 0644 <<'EOF'
# SPDX-License-Identifier: GPL-2.0-or-later
[Unit]
Description=Phosh on-screen keyboard
PartOf=graphical-session.target
After=graphical-session-pre.target
Before=mobi.phosh.Shell.service

[Service]
Type=dbus
BusName=sm.puri.OSK0
ExecStart=/usr/bin/squeekboard
Restart=on-failure
RestartSec=1
TimeoutStartSec=20
EOF
	write_root_file "$root/etc/systemd/user/mobi.phosh.Shell.service.d/keyboard.conf" 0644 <<'EOF'
# SPDX-License-Identifier: GPL-2.0-or-later
[Unit]
Wants=kernarium-squeekboard.service
After=kernarium-squeekboard.service
EOF
	write_root_file "$root/usr/share/dbus-1/services/sm.puri.OSK0.service" 0644 <<'EOF'
# SPDX-License-Identifier: GPL-2.0-or-later
[D-BUS Service]
Name=sm.puri.OSK0
Exec=/usr/bin/squeekboard
SystemdService=kernarium-squeekboard.service
EOF
	write_root_file "$root/etc/dconf/profile/user" 0644 <<'EOF'
user-db:user
system-db:local
EOF
	write_root_file "$root/etc/dconf/db/local.d/10-kernarium" 0644 <<'EOF'
[org/gnome/shell]
favorite-apps=['org.gnome.Console.desktop']

[org/gnome/desktop/input-sources]
sources=[('xkb', 'us'), ('xkb', 'ru')]

[org/gnome/desktop/a11y/applications]
screen-keyboard-enabled=true

[sm/puri/phosh/lockscreen]
# The initial account has no password; Phosh refuses an empty unlock entry.
require-unlock=false
EOF
	as_root chroot "$root" dconf update
}
