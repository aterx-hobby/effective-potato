#!/bin/bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:0}

# If no command provided, default to infinite sleep
if [ "$#" -eq 0 ]; then
  set -- sleep infinity
fi

# Start supervisord (manages X server)
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf &

# Wait for X socket to be ready (up to 20s)
for i in $(seq 1 40); do
  if [ -S "/tmp/.X11-unix/X${DISPLAY#:}" ]; then
    break
  fi
  sleep 0.5
done

# Allow ubuntu user to access DISPLAY
if command -v xhost >/dev/null 2>&1; then
  xhost +SI:localuser:ubuntu || true
  xhost +local: || true
fi

# Ensure ubuntu user exists and owns workspace
if ! id -u ubuntu >/dev/null 2>&1; then
  useradd -ms /bin/bash ubuntu
fi
chown -R ubuntu:ubuntu /workspace || true

# Start XFCE session as ubuntu in background, write logs to /tmp
su -s /bin/bash -c 'export DISPLAY="${DISPLAY}"; nohup dbus-launch startxfce4 > /tmp/xfce4.log 2>&1 & disown' ubuntu || true

# Exec the main command as ubuntu with robust quoting
if command -v runuser >/dev/null 2>&1; then
  exec runuser -u ubuntu -- "$@"
else
  exec su -s /bin/bash -c 'exec "$@"' ubuntu -- "$@"
fi
