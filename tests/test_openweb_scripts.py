import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class MockHandler(BaseHTTPRequestHandler):
    models = [
        {"id": "1", "name": "effective-potato", "label": "effective-potato", "meta": {"v": 1}},
        {"id": "2", "name": "other-model", "label": "other-model", "meta": {"v": 1}},
    ]
    last_import = None

    def do_GET(self):  # noqa: N802
        if self.path == "/api/v1/models/export":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.models).encode("utf-8"))
        elif self.path.startswith("/api/v1/models"):
            # list models
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.models).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):  # noqa: N802
        if self.path.startswith("/api/v1/models/model/delete"):
            # simulate delete by id
            import urllib.parse as up
            q = up.urlparse(self.path).query
            params = dict(up.parse_qsl(q))
            mid = params.get("id")
            self.__class__.models = [m for m in self.__class__.models if m.get("id") != mid]
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path == "/api/v1/models/import":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            self.__class__.last_import = data
            name = data.get("name") or data.get("model_name")
            # simulate creation with new id=99 when name unique
            exists = any((m.get("name") == name) for m in self.__class__.models)
            if exists:
                self.send_response(422)
                self.end_headers()
                return
            self.__class__.models.append({"id": "99", "name": name, "label": name, "meta": {"v": 1}})
            self.send_response(201)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def run_server(port, ready_evt):
    httpd = HTTPServer(("127.0.0.1", port), MockHandler)
    ready_evt.set()
    httpd.serve_forever()


def test_export_script(tmp_path, monkeypatch):
    # start mock server
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    evt = threading.Event()
    t = threading.Thread(target=run_server, args=(port, evt), daemon=True)
    t.start(); evt.wait(1)

    env = os.environ.copy()
    env["DEV_OPENWEB_URL"] = f"http://127.0.0.1:{port}"
    env["DEV_OPENWEBAPI_URL"] = f"http://127.0.0.1:{port}"
    env["DEV_OPENWEB_KEY"] = "dummy"
    env["DEV_OPENWEBAPI_KEY"] = "dummy"
    env["MODEL_NAME"] = "effective-potato"
    env["OUTPUT_DIR"] = str(tmp_path / "out")

    script = Path("scripts/export_openweb_model.sh").resolve()
    assert script.exists()

    import subprocess
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=5)
    assert res.returncode == 0, res.stderr
    assert "Exported:" in res.stdout
    # Ensure file exists
    outdir = tmp_path / "out"
    files = list(outdir.glob("*.json"))
    assert files, "no export file produced"


def test_import_script_alt_name_and_delete(tmp_path, monkeypatch):
    # start mock server with fresh state
    MockHandler.models = [
        {"id": "1", "name": "effective-potato", "label": "effective-potato"},
    ]
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    evt = threading.Event(); t = threading.Thread(target=run_server, args=(port, evt), daemon=True); t.start(); evt.wait(1)

    # create an export JSON with name=effective-potato
    export_json = tmp_path / "export.json"
    export_json.write_text(json.dumps({"name": "effective-potato", "meta": {"v": 1}}))

    script = Path("scripts/import_openweb_model.sh").resolve()
    assert script.exists()

    import subprocess
    env = os.environ.copy()
    env["DEV_OPENWEB_URL"] = f"http://127.0.0.1:{port}"
    env["DEV_OPENWEB_KEY"] = "dummy"
    env["MODEL_FILE"] = str(export_json)

    # 1) Import with alternative name; should not clobber existing
    env["NEW_MODEL_NAME"] = "effective-potato-alt"
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=5)
    assert res.returncode == 0, res.stderr
    assert "Imported: effective-potato-alt" in res.stdout

    # 2) Attempt to import again without delete; should warn and not overwrite when using same alt name
    res2 = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=5)
    assert res2.returncode == 0
    assert "WARNING: Model 'effective-potato-alt' already exists" in (res2.stderr or res2.stdout)

    # 3) Now import with delete existing = 1; should recreate
    env["DELETE_EXISTING"] = "1"
    res3 = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=5)
    assert res3.returncode == 0
    assert "Imported: effective-potato-alt" in res3.stdout
