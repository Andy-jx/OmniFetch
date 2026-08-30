from __future__ import annotations

import threading

import server_v3 as v3

core = v3.core
core.VERSION = "0.5.4"

_tls = threading.local()
_original_common_ydl_options = core.common_ydl_options
_original_probe_media = core.probe_media

_ALLOWED_HEADERS = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "cookie": "Cookie",
    "accept": "Accept",
    "accept-language": "Accept-Language",
}


def sanitize_request_headers(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    result: dict[str, str] = {}
    total = 0
    for key, value in raw.items():
        canonical = _ALLOWED_HEADERS.get(str(key).strip().lower())
        if not canonical:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        limit = 32768 if canonical == "Cookie" else 4096
        text = text[:limit]
        total += len(text)
        if total > 49152:
            break
        result[canonical] = text
    return result


def current_headers() -> dict[str, str]:
    return dict(getattr(_tls, "request_headers", {}) or {})


def common_ydl_options_v4(page_url: str) -> dict:
    opts = _original_common_ydl_options(page_url)
    headers = dict(opts.get("http_headers") or {})
    headers.update(current_headers())
    if page_url and not headers.get("Referer"):
        headers["Referer"] = page_url
    opts["http_headers"] = headers
    return opts


def probe_media_v4(payload: dict) -> dict:
    _tls.request_headers = sanitize_request_headers(payload.get("request_headers"))
    try:
        return _original_probe_media(payload)
    finally:
        _tls.request_headers = {}


def run_download_v4(job_id: str, payload: dict) -> None:
    _tls.request_headers = sanitize_request_headers(payload.get("request_headers"))
    try:
        v3.run_download(job_id, payload)
    finally:
        _tls.request_headers = {}


core.common_ydl_options = common_ydl_options_v4
core.probe_media = probe_media_v4
core.run_download = run_download_v4


if __name__ == "__main__":
    core.main()
