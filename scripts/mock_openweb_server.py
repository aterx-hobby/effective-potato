#!/usr/bin/env python3
"""
Standalone mock OpenWeb API server to debug import/export scripts.

Endpoints:
- GET  /api/v1/models/export -> list models (JSON array)
- GET  /api/v1/models        -> list models (JSON array)
- POST /api/v1/models/import -> create when name unique, else 422
- DELETE /api/v1/models/model/delete?id=<id> -> delete and return 200

Usage:
  python3 scripts/mock_openweb_server.py --port 18081
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import json
import threading
import urllib.parse as up


class MockHandler(BaseHTTPRequestHandler):
    models = [
        {"id": "1", "name": "effective-potato", "label": "effective-potato", "meta": {"v": 1}},
        {"id": "2", "name": "other-model", "label": "other-model", "meta": {"v": 1}},
    ]
    last_import = None

    def log_message(self, format, *args):  # noqa: A003 - silence default logging
        return

    def do_GET(self):  # noqa: N802
        if self.path == "/api/v1/models/export" or self.path.startswith("/api/v1/models"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.__class__.models).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):  # noqa: N802
        if self.path.startswith("/api/v1/models/model/delete"):
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
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            self.__class__.last_import = data
            name = (data.get("name") or data.get("model_name") or "").strip()
            if not name:
                self.send_response(422)
                self.end_headers()
                return
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18081)
    args = ap.parse_args()
    httpd = HTTPServer(("127.0.0.1", args.port), MockHandler)
    print(f"Mock OpenWeb API server on http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
