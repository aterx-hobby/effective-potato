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
        if self.path.startswith("/api/v1/models") and not self.path.endswith("/export"):
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

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/api/v1/models/create"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            name = data.get("name") or data.get("id")
            if any(m.get("name") == name for m in self.__class__.models):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"Uh-oh! Model already registered.")
                return
            self.__class__.models.append({"id": name, "name": name, "label": name, "meta": data.get("meta") or {}, "params": data.get("params") or {}})
            self.send_response(200)
            self.end_headers()
        elif self.path.startswith("/api/v1/models/model/update"):
            import urllib.parse as up
            q = up.urlparse(self.path).query
            params = dict(up.parse_qsl(q))
            name = params.get("id")
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


def test_register_and_publish(tmp_path):
    # Fresh server
    MockHandler.models = [{"id": "1", "name": "effective-potato", "label": "effective-potato", "meta": {"v": 1}, "params": {"p": 1}}]
    MockHandler.configs = {"DEFAULT_MODELS": None, "MODEL_ORDER_LIST": []}

    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    evt = threading.Event(); t = threading.Thread(target=run_server, args=(port, evt), daemon=True); t.start(); evt.wait(1)

    script_reg = Path("scripts/register_openweb_model.sh").resolve()
    script_pub = Path("scripts/publish_openweb_model.sh").resolve()
    assert script_reg.exists() and script_pub.exists()

    # Prepare a minimal export JSON to copy params/meta
    exp = tmp_path / "export.json"
    exp.write_text(json.dumps({"name": "effective-potato", "meta": {"desc": "x"}, "params": {"p": 2}}))

    import subprocess
    env = os.environ.copy()
    env["DEV_OPENWEB_URL"] = f"http://127.0.0.1:{port}"
    env["DEV_OPENWEB_KEY"] = "dummy"

    # Register new model under alt name; should create and then publish as default
    env_reg = env.copy()
    env_reg["MODEL_FILE"] = str(exp)
    env_reg["NEW_MODEL_NAME"] = "silly-pertato"
    env_reg["SET_DEFAULT"] = "1"
    res = subprocess.run(["bash", str(script_reg)], capture_output=True, text=True, env=env_reg, timeout=8)
    assert res.returncode == 0, res.stderr
    assert "Published to workspace order: silly-pertato" in res.stdout

    # Publish script should also allow setting default and ordering
    env_pub = env.copy()
    env_pub["MODEL_NAME"] = "silly-pertato"
    env_pub["SET_DEFAULT"] = "1"
    env_pub["POSITION"] = "front"
    res2 = subprocess.run(["bash", str(script_pub)], capture_output=True, text=True, env=env_pub, timeout=8)
    assert res2.returncode == 0, res2.stderr
    assert "Published to workspace: silly-pertato" in res2.stdout

    # Validate server-side state
    assert MockHandler.configs.get("DEFAULT_MODELS") == "silly-pertato"
    assert MockHandler.configs.get("MODEL_ORDER_LIST", [])[0] == "silly-pertato"

    # Re-register path hits update fallback
    res3 = subprocess.run(["bash", str(script_reg)], capture_output=True, text=True, env=env_reg, timeout=8)
    assert res3.returncode == 0
    assert ("Updated UI model: silly-pertato" in res3.stdout) or ("Registered in UI: silly-pertato" in res3.stdout)
