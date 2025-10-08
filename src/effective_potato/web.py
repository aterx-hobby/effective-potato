"""Web/metrics utilities.

Note: The project no longer runs an embedded HTTP server. This module
retains only small helpers (metrics counters). URL builders and Flask
app utilities have been removed.
"""

from __future__ import annotations

import threading
import logging


__all__ = [
    "record_tool_metric",
    "render_metrics_text",
]


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


def get_http_log_level():  # retained for compatibility if used elsewhere
    try:
        return logging.WARNING
    except Exception:
        return logging.WARNING
