#!/bin/bash
set -euo pipefail

# Ensure a runtime directory for X sockets
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Try to start Xorg with fbdev; fall back to Xvfb if not available
start_xorg_fbdev() {
  if command -v Xorg >/dev/null 2>&1; then
    echo "Starting Xorg (fbdev) on DISPLAY=${DISPLAY}"
    # Create a minimal xorg.conf for fbdev if needed
    XORG_CONF=/etc/X11/xorg.conf
    if [ ! -f "$XORG_CONF" ]; then
      mkdir -p /etc/X11
      cat > "$XORG_CONF" <<'EOF'
Section "Device"
    Identifier  "FBDEV"
    Driver      "fbdev"
EndSection

Section "Monitor"
    Identifier  "DummyMonitor"
EndSection

Section "Screen"
    Identifier  "Screen0"
    Device      "FBDEV"
    Monitor     "DummyMonitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1024x768"
    EndSubSection
EndSection

Section "ServerLayout"
    Identifier  "Layout0"
    Screen      "Screen0"
EndSection
EOF
    fi

    # Start Xorg in background
    nohup Xorg "${DISPLAY}" vt1 -noreset -novtswitch -sharevts -verbose 3 >/var/log/xorg.log 2>&1 &
    sleep 2
    if pgrep -x Xorg >/dev/null; then
      echo "Xorg started successfully"
      return 0
    fi
  fi
  return 1
}

start_xvfb() {
  echo "Starting Xvfb on DISPLAY=${DISPLAY}"
  nohup Xvfb "${DISPLAY}" -screen 0 1024x768x24 >/var/log/xvfb.log 2>&1 &
  sleep 1
}

if ! start_xorg_fbdev; then
  start_xvfb
fi

echo "X server initialized"
exit 0
