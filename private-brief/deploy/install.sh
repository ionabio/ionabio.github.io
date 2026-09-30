#!/bin/sh
# Reviewable installation scaffold. DOES NOT configure network, generate credentials,
# acquire certificates, enable services, or alter the existing website.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run after approved SSH deployment as root'; exit 1; }
command -v python3 >/dev/null
id nabi-brief >/dev/null 2>&1 || useradd --system --home /var/lib/nabi-brief --shell /usr/sbin/nologin nabi-brief
install -d -o root -g root -m 0755 /opt/nabi-brief
install -d -o nabi-brief -g nabi-brief -m 0700 /var/lib/nabi-brief
install -d -o root -g root -m 0700 /etc/nabi-brief
# Invoke from private-brief directory, containing source code only.
cp -R brief templates assets requirements.txt /opt/nabi-brief/
python3 -m venv /opt/nabi-brief/.venv
/opt/nabi-brief/.venv/bin/pip install -r /opt/nabi-brief/requirements.txt
install -m 0644 deploy/nabi-brief.service deploy/nabi-publish.service deploy/nabi-publish.timer /etc/systemd/system/
echo 'Installed source and unit files. Approved secrets, connectivity, TLS and enablement are still required.'
