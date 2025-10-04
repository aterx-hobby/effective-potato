#!/bin/bash
set -euo pipefail

# Ensure runtime directory for X sockets
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

export DISPLAY=${DISPLAY:-:0}

# Prefer Xvfb in container; fallback to Xorg if needed
if command -v Xvfb >/dev/null 2>&1; then
  exec Xvfb "${DISPLAY}" -screen 0 1024x768x24
elif command -v Xorg >/dev/null 2>&1; then
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
  exec Xorg "${DISPLAY}" vt1 -noreset -novtswitch -sharevts -verbose 3
else
  echo "No X server found (Xvfb/Xorg missing)." >&2
  exit 1
fi
