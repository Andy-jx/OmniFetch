from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

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
    "authorization": "Authorization",
    "accept": "Accept",
    "accept-language": "Accept-Language",
}


def sanitize_request_headers(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    result: dict[str, str] = {}
    total = 0
    for key, value in raw.items():
        lower = str(key).strip().lower()
        canonical = _ALLOWED_HEADERS.get(lower)
        if not canonical and lower.startswith("x-") and re.fullmatch(r"x-[a-z0-9-]{1,80}", lower):
            canonical = str(key).strip()[:96]
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
    opts["socket_timeout"] = 15
    opts["retries"] = 3
    opts["fragment_retries"] = 4
    return opts


def probe_media_v4(payload: dict) -> dict:
    _tls.request_headers = sanitize_request_headers(payload.get("request_headers"))
    try:
        return _original_probe_media(payload)
    finally:
        _tls.request_headers = {}


def ffmpeg_executable() -> str | None:
    folder = core.find_ffmpeg()
    if folder:
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidate = Path(folder) / name
            if candidate.exists():
                return str(candidate)
    return shutil.which("ffmpeg")


def ffmpeg_headers_arg(headers: dict[str, str]) -> str:
    skip = {"User-Agent", "Referer"}
    lines = [f"{key}: {value}" for key, value in headers.items() if key not in skip and value]
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def safe_title(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip().strip(".")
    return text[:140] or "video"


def try_ffmpeg_manifest(job_id: str, payload: dict, previous_error: str = "") -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    if not media_url or not v3.is_manifest_url(media_url):
        return False

    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return False

    headers = current_headers()
    page_url = str(payload.get("page_url") or "").strip()
    title = safe_title(payload.get("title") or "video")
    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-ffmpeg"
    v3.safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_output = temp_dir / "stream.mp4"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    user_agent = headers.get("User-Agent")
    referer = headers.get("Referer") or page_url
    header_block = ffmpeg_headers_arg(headers)
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referer:
        cmd += ["-referer", referer]
    if header_block:
        cmd += ["-headers", header_block]
    cmd += [
        "-i", media_url,
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c", "copy",
        "-movflags", "+faststart",
        str(temp_output),
    ]

    core.update_job(
        job_id,
        status="downloading",
        percent=None,
        strategy="ffmpeg-manifest-fallback",
        target_url=media_url,
        error=previous_error or None,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "FFmpeg HLS download failed").strip()[-1800:]
            raise RuntimeError(detail)
        if not temp_output.exists() or temp_output.stat().st_size < v3.MIN_VIDEO_BYTES:
            size = temp_output.stat().st_size if temp_output.exists() else 0
            raise RuntimeError(f"FFmpeg 下载结果异常，仅 {size} 字节")

        core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        destination = v3.unique_destination(core.DOWNLOAD_DIR / f"{title}.mp4")
        shutil.move(str(temp_output), str(destination))
        v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="completed",
            percent=100,
            title=title,
            output_dir=str(core.DOWNLOAD_DIR),
            output_file=str(destination),
            output_bytes=destination.stat().st_size,
            download_kind="video",
            strategy="ffmpeg-manifest-fallback",
            error=None,
        )
        return True
    except Exception as exc:
        v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="failed",
            error=f"{previous_error}\nFFmpeg fallback: {exc}".strip(),
            strategy="ffmpeg-manifest-fallback",
        )
        return False


def run_download_v4(job_id: str, payload: dict) -> None:
    _tls.request_headers = sanitize_request_headers(payload.get("request_headers"))
    try:
        v3.run_download(job_id, payload)
        kind = str(payload.get("download_kind") or "video").strip().lower()
        if kind != "video":
            return
        with core.JOBS_LOCK:
            job = dict(core.JOBS.get(job_id) or {})
        if job.get("status") == "failed" and v3.is_manifest_url(str(payload.get("media_url") or "")):
            try_ffmpeg_manifest(job_id, payload, str(job.get("error") or ""))
    finally:
        _tls.request_headers = {}


core.common_ydl_options = common_ydl_options_v4
core.probe_media = probe_media_v4
core.run_download = run_download_v4


if __name__ == "__main__":
    core.main()
