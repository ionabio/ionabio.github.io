#!/bin/sh
# Run from reviewed staged deploy directory only AFTER specific updater/access approval.
set -eu
test "$(id -u)" = 0
test "$(uname -m)" = aarch64
test -d /opt/nabi-brief/.venv
test -f /etc/nabi-brief/environment
test ! -e /usr/local/lib/nabi-release/updater.py
umask 077
install -d -m 0755 /usr/local/lib/nabi-release /opt/nabi-brief-releases
install -d -m 0700 /var/lib/nabi-release
# Pinned official GitHub CLI, checked against inspected vendor release digest.
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
curl --fail --location --proto '=https' --proto-redir '=https' --max-time 120 -o "$scratch/gh.tar.gz" https://github.com/cli/cli/releases/download/v2.102.0/gh_2.102.0_linux_arm64.tar.gz
printf '%s  %s\n' 7862c86c72f43df3a2d93ddde6f473285b4e2af61b494849846827e513ef6484 "$scratch/gh.tar.gz" | sha256sum -c -
tar -xzf "$scratch/gh.tar.gz" -C "$scratch" gh_2.102.0_linux_arm64/bin/gh
install -m 0755 "$scratch/gh_2.102.0_linux_arm64/bin/gh" /usr/local/lib/nabi-release/gh
install -m 0644 updater.py verify_candidate.py /usr/local/lib/nabi-release/
sha256sum ../../.github/workflows/brief-release.yml | cut -d ' ' -f 1 > /usr/local/lib/nabi-release/workflow.sha256
install -m 0644 nabi-release-update.service nabi-release-update.timer /etc/systemd/system/
test ! -e /opt/nabi-brief-releases/current
ln -s /opt/nabi-brief /opt/nabi-brief-releases/current
install -d -m 0755 /etc/systemd/system/nabi-brief.service.d /etc/systemd/system/nabi-publish.service.d
for unit in nabi-brief nabi-publish; do
    test ! -e "/etc/systemd/system/$unit.service.d/release-path.conf"
    printf '%s\n' '[Service]' 'WorkingDirectory=/opt/nabi-brief-releases/current' 'ExecStart=' > "/etc/systemd/system/$unit.service.d/release-path.conf"
done
printf '%s\n' 'ExecStart=/opt/nabi-brief-releases/current/.venv/bin/python -m brief serve' >> /etc/systemd/system/nabi-brief.service.d/release-path.conf
printf '%s\n' 'ExecStart=/opt/nabi-brief-releases/current/.venv/bin/python -m brief publish --notify' >> /etc/systemd/system/nabi-publish.service.d/release-path.conf
# Old app and all private state/settings remain in place. Publication stays disabled.
systemd-analyze verify /etc/systemd/system/nabi-release-update.service /etc/systemd/system/nabi-release-update.timer
systemctl daemon-reload
systemctl restart nabi-brief.service
curl --fail --silent --retry 10 --retry-connrefused --max-time 10 http://127.0.0.1:8080/health
systemctl enable --now nabi-release-update.timer
if systemctl is-enabled --quiet nabi-publish.timer; then
    echo 'Publication timer unexpectedly enabled; stop for review' >&2
    exit 1
fi
printf '\nUPDATER_BOOTSTRAPPED_PUBLICATION_TIMER_UNCHANGED\n'
