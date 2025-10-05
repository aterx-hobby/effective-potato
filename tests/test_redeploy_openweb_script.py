import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class MockHandler(BaseHTTPRequestHandler):
    models = [
        {"id": "1", "name": "effective-potato", "label": "effective-potato", "meta": {"v": 1}, "params": {"p": 1}},
    ]
    configs = {"DEFAULT_MODELS": None, "MODEL_ORDER_LIST": []}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/v1/models/export"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.models).encode("utf-8"))
        elif self.path.startswith("/api/v1/models"):
            # return list
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.models).encode("utf-8"))
        elif self.path.startswith("/api/v1/configs/models"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.configs).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):  # noqa: N802
        if self.path.startswith("/api/v1/models/model/delete"):
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
        if self.path.startswith("/api/v1/models/import"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            # Accept wrapped {"models":[...]}
            if isinstance(data, dict) and "models" in data and isinstance(data["models"], list):
                data = data["models"][0]
            target = data.get("name") or data.get("model_name")
            # conflict if exists
            if any(m.get("name") == target for m in self.__class__.models):
                self.send_response(422)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{\"detail\":[{\"loc\":[\"body\",\"models\"],\"msg\":\"Field required\"}]}")
                return
            self.__class__.models.append({"id": "99", "name": target, "label": target, "meta": {"v": 1}, "params": {}})
            self.send_response(201)
            self.end_headers()
        elif self.path.startswith("/api/v1/models/create"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            name = data.get("name") or data.get("id")
            # already registered if exists
            if any(m.get("name") == name for m in self.__class__.models):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"Uh-oh! Model already registered.")
                return
            # Append a registry entry (simulate UI)
            self.__class__.models.append({"id": name, "name": name, "label": name, "meta": data.get("meta") or {}, "params": data.get("params") or {}})
            self.send_response(200)
            self.end_headers()
        elif self.path.startswith("/api/v1/models/model/update"):
            import urllib.parse as up
            q = up.urlparse(self.path).query
            params = dict(up.parse_qsl(q))
            name = params.get("id")
            # ensure present; update meta/params
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            for m in self.__class__.models:
                if m.get("name") == name:
                    m.update({"meta": data.get("meta") or {}, "params": data.get("params") or {}})
                    break
            else:
                self.__class__.models.append({"id": name, "name": name, "label": name, "meta": data.get("meta") or {}, "params": data.get("params") or {}})
            self.send_response(200)
            self.end_headers()
        elif self.path.startswith("/api/v1/configs/models"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            self.__class__.configs = data
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def run_server(port, ready_evt):
    httpd = HTTPServer(("127.0.0.1", port), MockHandler)
    ready_evt.set()
    httpd.serve_forever()


def test_redeploy_end_to_end(tmp_path):
    # fresh server
    MockHandler.models = [{"id": "1", "name": "effective-potato", "label": "effective-potato", "meta": {"v": 1}, "params": {"p": 1}}]
    MockHandler.configs = {"DEFAULT_MODELS": None, "MODEL_ORDER_LIST": []}

    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    evt = threading.Event(); t = threading.Thread(target=run_server, args=(port, evt), daemon=True); t.start(); evt.wait(1)

    # Prepare a temporary export dir
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    # Run redeploy script
    script = Path("scripts/redeploy_openweb_model.sh").resolve()
    assert script.exists()

    import subprocess
    env = os.environ.copy()
    env["DEV_OPENWEB_URL"] = f"http://127.0.0.1:{port}"
    env["DEV_OPENWEB_KEY"] = "dummy"
    env["MODEL_NAME"] = "effective-potato"
    env["NEW_MODEL_NAME"] = "silly-pertato"
    env["SET_DEFAULT"] = "1"
    env["OUTPUT_DIR"] = str(export_dir)

    res = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=8)
    assert res.returncode == 0, res.stderr
    assert "Redeployed: silly-pertato" in res.stdout

    # Verify that configs were updated to include silly-pertato as default
    assert MockHandler.configs.get("DEFAULT_MODELS") == "silly-pertato"
    assert "silly-pertato" in MockHandler.configs.get("MODEL_ORDER_LIST", [])

    # Second run should be idempotent without DELETE_EXISTING
    res2 = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=8)
    assert res2.returncode == 0
    assert "Redeployed: silly-pertato" in res2.stdout
