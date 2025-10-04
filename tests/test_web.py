import os
import types
from pathlib import Path

import pytest

from effective_potato.web import (
    detect_public_host,
    get_server_config,
    build_screenshot_url,
    get_http_log_level,
    create_app,
)


def test_url_building_and_config(monkeypatch, tmp_path: Path):
    # Defaults
    monkeypatch.delenv("EFFECTIVE_POTATO_HOSTNAME", raising=False)
    monkeypatch.delenv("EFFECTIVE_POTATO_IP", raising=False)
    monkeypatch.delenv("EFFECTIVE_POTATO_PORT", raising=False)

    bind_ip, port, public_host = get_server_config()
    assert bind_ip == "0.0.0.0"
    assert isinstance(port, int) and port > 0
    assert isinstance(public_host, str) and len(public_host) > 0

    url = build_screenshot_url(public_host, port, "shot.png")
    assert url.endswith("/screenshots/shot.png")

    # Env overrides
    monkeypatch.setenv("EFFECTIVE_POTATO_HOSTNAME", "example.com")
    monkeypatch.setenv("EFFECTIVE_POTATO_IP", "1.2.3.4")
    monkeypatch.setenv("EFFECTIVE_POTATO_PORT", "9099")

    bind_ip2, port2, public_host2 = get_server_config()
    assert bind_ip2 == "1.2.3.4"
    assert port2 == 9099
    assert public_host2 == "example.com"


def test_http_log_level(monkeypatch):
    from logging import WARNING, INFO, ERROR
    monkeypatch.setenv("EFFECTIVE_POTATO_HTTP_LOG", "info")
    assert get_http_log_level() == INFO
    monkeypatch.setenv("EFFECTIVE_POTATO_HTTP_LOG", "warning")
    assert get_http_log_level() == WARNING
    monkeypatch.setenv("EFFECTIVE_POTATO_HTTP_LOG", "error")
    assert get_http_log_level() == ERROR
    monkeypatch.setenv("EFFECTIVE_POTATO_HTTP_LOG", "silent")
    assert get_http_log_level() is None


def test_create_app_routes(tmp_path: Path):
    ws = tmp_path
    app = create_app(ws)
    # Ensure the screenshots directory exists
    d = ws / ".agent_screenshots"
    assert d.exists() and d.is_dir()

    # Use Flask test client to simulate requests
    client = app.test_client()

    # health check
    resp = client.get("/healthz")
    assert resp.status_code == 200

    # missing screenshot -> 404
    resp = client.get("/screenshots/does-not-exist.png")
    assert resp.status_code == 404

    # create a fake file and serve
    f = d / "ok.png"
    f.write_bytes(b"fakepng")
    resp = client.get("/screenshots/ok.png")
    assert resp.status_code == 200
    assert resp.data == b"fakepng"
