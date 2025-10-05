"""HTTP server for hosting artifacts like screenshots."""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

import logging
from flask import Flask, send_from_directory, abort, request
from werkzeug.serving import make_server
from urllib.parse import quote


def _first_ipv4(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
    if not m:
        return None
    ip = m.group(1)
    # basic sanity check
    parts = ip.split(".")
    if all(0 <= int(p) <= 255 for p in parts):
        return ip
    return None


def detect_public_host() -> str:
    """Detect a public-facing hostname/IP for building URLs.

    Priority:
    - EFFECTIVE_POTATO_HOSTNAME
    - EFFECTIVE_POTATO_IP (when not 0.0.0.0)
    - host $HOSTNAME
    - socket.gethostbyname(gethostname())
    - localhost
    """
    env_host = os.getenv("EFFECTIVE_POTATO_HOSTNAME")
    if env_host:
        return env_host.strip()

    env_ip = os.getenv("EFFECTIVE_POTATO_IP")
    if env_ip and env_ip.strip() and env_ip.strip() != "0.0.0.0":
        return env_ip.strip()

    # Try `host $HOSTNAME`
    try:
        cmd = 'host "$HOSTNAME"'
        out = subprocess.check_output(["bash", "-lc", cmd], stderr=subprocess.STDOUT, text=True, timeout=3.0)
        ip = _first_ipv4(out)
        if ip:
            return ip
    except Exception:
        pass

    # Fallback to socket
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        pass

    return "localhost"


def get_server_config() -> Tuple[str, int, str]:
    """Return (bind_ip, port, public_host) based on env/defaults."""
    bind_ip = os.getenv("EFFECTIVE_POTATO_IP", "0.0.0.0")
    try:
        port = int(os.getenv("EFFECTIVE_POTATO_PORT", "9090"))
    except ValueError:
        port = 9090
    public_host = detect_public_host()
    return bind_ip, port, public_host


def build_base_url(public_host: str, port: int) -> str:
    return f"http://{public_host}:{port}"


def build_screenshot_url(public_host: str, port: int, filename: str) -> str:
    # Avoid double slashes
    fname = filename.lstrip("/")
    return f"{build_base_url(public_host, port)}/screenshots/{quote(fname)}"


def get_tool_schema_url() -> str:
    """Return the tool schema URL from env or a sensible default.

    EFFECTIVE_POTATO_TOOL_SCHEMA_URL may be set directly. If not set,
    the default is built using the detected public host:
        http://$EFFECTIVE_POTATO_HOSTNAME:8000/effective-potato/openapi.json
    where $EFFECTIVE_POTATO_HOSTNAME is the result of detect_public_host().
    """
    env_url = os.getenv("EFFECTIVE_POTATO_TOOL_SCHEMA_URL")
    if env_url:
        return env_url.strip()
    host = detect_public_host()
    return f"http://{host}:8000/effective-potato/openapi.json"


def get_http_log_level() -> Optional[int]:
    """Parse EFFECTIVE_POTATO_HTTP_LOG into a logging level.

    Supported values: critical, error, warn|warning, info, debug, silent|off|none.
    Returns None for silent (to disable), else an int logging level.
    Default is WARNING.
    """
    val = os.getenv("EFFECTIVE_POTATO_HTTP_LOG", "").strip().lower()
    if not val:
        return logging.WARNING
    mapping = {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "silent": None,
        "off": None,
        "none": None,
    }
    return mapping.get(val, logging.WARNING)


_metrics_lock = threading.Lock()
_metrics = {
    "up": 1,
    "requests_total": 0,
    "tool_calls_total": {},  # name -> count
    "tool_duration_ms": {},  # name -> total ms
}


def record_tool_metric(name: str, duration_ms: int) -> None:
    with _metrics_lock:
        _metrics["requests_total"] += 1
        _metrics["tool_calls_total"][name] = _metrics["tool_calls_total"].get(name, 0) + 1
        _metrics["tool_duration_ms"][name] = _metrics["tool_duration_ms"].get(name, 0) + max(0, int(duration_ms))


def render_metrics_text() -> str:
    lines = [
        f"effective_potato_up {_metrics.get('up', 0)}",
        f"effective_potato_requests_total {_metrics.get('requests_total', 0)}",
    ]
    calls = _metrics.get("tool_calls_total", {}) or {}
    for name, count in sorted(calls.items()):
        lines.append(f"effective_potato_tool_calls_total{{tool=\"{name}\"}} {count}")
    durs = _metrics.get("tool_duration_ms", {}) or {}
    for name, total_ms in sorted(durs.items()):
        lines.append(f"effective_potato_tool_duration_ms_sum{{tool=\"{name}\"}} {total_ms}")
    return "\n".join(lines) + "\n"


def create_app(workspace_dir: Path) -> Flask:
    app = Flask(__name__)

    screenshots_dir = workspace_dir / ".agent" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/healthz")
    def healthz():  # type: ignore[no-redef]
        return {"status": "ok"}, 200

    # Text metrics for quick scraping
    @app.get("/metrics")
    def metrics():  # type: ignore[no-redef]
        return (render_metrics_text(), 200, {"Content-Type": "text/plain; version=0.0.4"})

    @app.get("/screenshots/<path:filename>")
    def serve_screenshot(filename: str):  # type: ignore[no-redef]
        target = screenshots_dir / filename
        if not target.exists() or not target.is_file():
            abort(404)
        return send_from_directory(screenshots_dir, filename)

    return app


def start_http_server(app: Flask, bind_ip: str, port: int) -> threading.Thread:
    """Start a WSGI server (Werkzeug) in a background daemon thread without writing banners to stdout."""
    # Configure werkzeug/Flask loggers according to env
    try:
        level = get_http_log_level()
        wlog = logging.getLogger("werkzeug")
        if level is None:
            wlog.disabled = True
        else:
            wlog.setLevel(level)
        # Flask app logger
        if level is None:
            app.logger.disabled = True  # type: ignore[attr-defined]
        else:
            app.logger.setLevel(level)  # type: ignore[attr-defined]
    except Exception:
        pass

    server = make_server(bind_ip, port, app)

    def run():
        # Serve forever; this shouldn't print startup banners
        try:
            server.serve_forever()
        except Exception:
            pass

    thread = threading.Thread(target=run, name="effective-potato-web", daemon=True)
    thread.start()
    return thread
