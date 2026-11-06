#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' 'Run this installer as root' >&2
  exit 1
fi

cd /opt/tg-event
if ! getent passwd tg-event >/dev/null; then
  useradd --system --user-group --home-dir /var/lib/tg-event --no-create-home --shell /usr/sbin/nologin tg-event
fi

install -d -m 750 -o tg-event -g tg-event /var/lib/tg-event /var/lib/tg-event/raw /var/lib/tg-event/parsed /var/lib/tg-event/state /var/lib/tg-event/sessions /var/backups/tg-event
python3 -m venv /opt/tg-event/.venv
/opt/tg-event/.venv/bin/python -m pip install -r requirements.txt
/opt/tg-event/.venv/bin/python deploy/init_config.py

if [ ! -e .env ] && [ ! -L .env ]; then
  ln -s /etc/tg-event.env .env
fi
if [ "$(readlink .env)" != /etc/tg-event.env ]; then
  printf '%s\n' 'Existing .env is not the expected deployment symlink; inspect it manually' >&2
  exit 1
fi

install -m 644 deploy/tg-event-admin.service deploy/tg-event-watch.service deploy/tg-event-backup.service deploy/tg-event-backup.timer /etc/systemd/system/
install -d -m 755 /etc/systemd/journald.conf.d
install -m 644 deploy/journald.conf /etc/systemd/journald.conf.d/tg-event.conf
systemctl restart systemd-journald
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/tg-event-admin.service /etc/systemd/system/tg-event-watch.service /etc/systemd/system/tg-event-backup.service /etc/systemd/system/tg-event-backup.timer
systemctl enable --now tg-event-admin.service tg-event-backup.timer
printf '%s\n' 'Admin and backup timer installed. Watcher remains disabled until credentials and Telegram authorization are configured.'
