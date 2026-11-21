# SPDX-License-Identifier: GPL-2.0-or-later

build_debian_minimal()
{
	printf '%s\n' "==> Building Debian 13 minimal GPT image"
	root=$roots_dir/debian-minimal
	prepare_debian_root "$root"
	configure_debian_base "$root" debian root
	configure_debian_local_console "$root" root
	as_root chroot "$root" /usr/bin/passwd -d root
	as_root systemctl --root="$root" set-default multi-user.target
	as_root chroot "$root" dpkg-query -W -f='${binary:Package}\t${Version}\n' | \
		sort > "$work_dir/debian-minimal.packages.tsv"
	unmount_chroot_proc
	cat "$root/etc/debian_version" > "$work_dir/debian.version"
	prune_debian "$root"
	destination=$(artifact_path debian-13-minimal.img)
	as_root python3 "$SCRIPT_DIR/make-gpt-image.py" \
		--root "$root" --output "$destination" --label debian-min \
		--headroom-mib 96
	as_root chown "$owner_uid:$owner_gid" "$destination"
	cp "$work_dir/debian-minimal.packages.tsv" "$destination.packages.tsv"
}
