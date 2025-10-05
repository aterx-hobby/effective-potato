"""Helpers for interacting with OpenWeb API to export workspace models.

This module focuses on deterministic, testable pieces: discovering a likely export
endpoint from an OpenAPI schema, building candidate URLs, choosing filenames by
content type, and building auth headers from env.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def build_auth_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "*/*"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def choose_filename(model_name: str, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("application/json", "text/json", "application/vnd.model+json"):
        ext = "json"
    elif ct in ("application/zip", "application/x-zip-compressed"):
        ext = "zip"
    elif ct in ("application/x-tar", "application/tar"):
        ext = "tar"
    elif ct in ("application/octet-stream", "binary/octet-stream"):
        ext = "bin"
    else:
        # default to .dat to avoid assumptions
        ext = "dat"
    safe = model_name.replace("/", "-")
    return f"{safe}.{ext}"


def make_candidate_export_urls(base_url: str, model_name: str) -> List[str]:
    base = base_url.rstrip("/")
    name = model_name
    candidates = [
        f"{base}/api/workspace/models/{name}/export",
        f"{base}/api/models/{name}/export",
        f"{base}/workspace/models/{name}/export",
        f"{base}/models/{name}/export",
    ]
    return candidates


def find_export_endpoint_from_openapi(openapi: Dict) -> Optional[Tuple[str, str]]:
    """Heuristically discover a model export endpoint from OpenAPI schema.

    Returns (method, path) like ("get", "/api/models/{name}/export") if found.
    """
    try:
        paths = openapi.get("paths") or {}
        for path, methods in paths.items():
            p = str(path).lower()
            if "export" in p and ("model" in p or "workspace" in p):
                # pick first method that looks usable
                for m in ("get", "post"):
                    if m in methods:
                        return m, path
    except Exception:
        pass
    return None
