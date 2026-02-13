"""Web/metrics utilities.

This module intentionally contains only lightweight, dependency-free helpers
(currently: in-process metrics counters). The MCP server is hosted separately
over Streamable HTTP.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, TypedDict


__all__ = [
    "record_tool_metric",
    "render_metrics_text",
]


class Metrics(TypedDict):
    up: int
    requests_total: int
    tool_calls_total: Dict[str, int]
    tool_duration_ms: Dict[str, int]


_metrics_lock: threading.Lock = threading.Lock()
# Strongly-typed metrics store
_metrics: Metrics = {
    "up": 1,
    "requests_total": 0,
    "tool_calls_total": {},
    "tool_duration_ms": {},
}


def record_tool_metric(name: str, duration_ms: int) -> None:
    with _metrics_lock:
        _metrics["requests_total"] = _metrics["requests_total"] + 1
        calls = _metrics["tool_calls_total"]
        total_ms = _metrics["tool_duration_ms"]
        calls[name] = calls.get(name, 0) + 1
        total_ms[name] = total_ms.get(name, 0) + max(0, int(duration_ms))


def render_metrics_text() -> str:
    lines = [
        f"effective_potato_up {_metrics['up']}",
        f"effective_potato_requests_total {_metrics['requests_total']}",
    ]
    calls: Dict[str, int] = _metrics["tool_calls_total"] or {}
    for name, count in sorted(calls.items()):
        lines.append(f"effective_potato_tool_calls_total{{tool=\"{name}\"}} {count}")
    durs: Dict[str, int] = _metrics["tool_duration_ms"] or {}
    for name, total_ms in sorted(durs.items()):
        lines.append(f"effective_potato_tool_duration_ms_sum{{tool=\"{name}\"}} {total_ms}")
    return "\n".join(lines) + "\n"


def get_http_log_level():  # retained for compatibility if used elsewhere
    try:
        return logging.WARNING
    except Exception:
        return logging.WARNING
