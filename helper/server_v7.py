from __future__ import annotations

import re
import subprocess

import server_v5 as v5

core = v5.core
core.VERSION = "0.5.7"

_original_select_best_hls = v5.select_best_hls


def _quality_p(width: int, height: int) -> int:
    if width > 0 and height > 0:
        return min(width, height)
    return height or width or 0


def _ffmpeg_probe_dimensions(url: str, headers: dict[str, str]) -> tuple[int, int]:
    ffmpeg = v5.ffmpeg_executable()
    if not ffmpeg or not url:
        return 0, 0

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info"]
    user_agent = headers.get("User-Agent")
    referer = headers.get("Referer")
    extra_headers = v5.header_block(headers)
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referer:
        cmd += ["-referer", referer]
    if extra_headers:
        cmd += ["-headers", extra_headers]

    # Decode only one frame. For a media playlist that has no RESOLUTION tag,
    # FFmpeg can still read the first segment and report the real coded size.
    cmd += [
        "-i", url,
        "-map", "0:v:0?",
        "-frames:v", "1",
        "-an",
        "-f", "null",
        "-",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return 0, 0

    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    for line in text.splitlines():
        if "Video:" not in line:
            continue
        match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", line)
        if not match:
            continue
        width, height = int(match.group(1)), int(match.group(2))
        if 80 <= width <= 16384 and 80 <= height <= 16384:
            return width, height
    return 0, 0


def select_best_hls_v7(media_url: str, headers: dict[str, str]) -> dict:
    selected = _original_select_best_hls(media_url, headers)
    width = int(selected.get("width") or 0)
    height = int(selected.get("height") or 0)
    if width <= 0 or height <= 0:
        probe_url = str(selected.get("video_url") or media_url)
        probed_width, probed_height = _ffmpeg_probe_dimensions(probe_url, headers)
        if probed_width and probed_height:
            selected["width"] = probed_width
            selected["height"] = probed_height
            selected["quality_p"] = _quality_p(probed_width, probed_height)
            selected["resolution_source"] = "ffmpeg-first-frame"
    else:
        selected["quality_p"] = _quality_p(width, height)
        selected["resolution_source"] = "hls-master"
    return selected


# Patch v5 in-place so both its probe path and its explicit HLS downloader use
# the same enriched selection result.
v5.select_best_hls = select_best_hls_v7


def probe_media_v7(payload: dict) -> dict:
    result = v5.probe_media_v5(payload)
    result["version"] = core.VERSION

    media_url = str(payload.get("media_url") or "").strip()
    if not media_url.lower().split("?", 1)[0].endswith(".m3u8"):
        return result

    headers = v5.v4.sanitize_request_headers(payload.get("request_headers"))
    page_url = str(payload.get("page_url") or "").strip()
    if page_url and not headers.get("Referer"):
        headers["Referer"] = page_url

    try:
        selected = select_best_hls_v7(media_url, headers)
    except Exception:
        return result

    width = int(selected.get("width") or 0)
    height = int(selected.get("height") or 0)
    if width <= 0 or height <= 0:
        return result

    quality_p = _quality_p(width, height)
    highest = v5.synthetic_best_format(selected)
    highest["height"] = height
    highest["width"] = width
    highest["quality_p"] = quality_p
    highest["format_note"] = (
        "OmniFetch detected HLS resolution"
        if not selected.get("is_master")
        else "OmniFetch explicit highest HLS variant"
    )

    old_formats = [
        item for item in (result.get("formats") or [])
        if item.get("format_id") != highest["format_id"]
    ]
    result["formats"] = [highest] + old_formats
    result["highest_hls"] = {
        "width": width,
        "height": height,
        "quality_p": quality_p,
        "bandwidth": int(selected.get("bandwidth") or 0),
        "is_master": bool(selected.get("is_master")),
        "resolution_source": selected.get("resolution_source") or "",
    }
    return result


def run_download_v7(job_id: str, payload: dict) -> None:
    # v5.run_download_v5 calls v5.run_ffmpeg_best_hls, which now calls the
    # patched v5.select_best_hls above. That makes selected_width/height visible
    # during the actual download even for child/media playlists.
    v5.run_download_v5(job_id, payload)
    with core.JOBS_LOCK:
        job = dict(core.JOBS.get(job_id) or {})
    width = int(job.get("selected_width") or 0)
    height = int(job.get("selected_height") or 0)
    if width and height:
        core.update_job(
            job_id,
            selected_quality_p=_quality_p(width, height),
            selected_quality=f"{width}x{height}",
        )


core.probe_media = probe_media_v7
core.run_download = run_download_v7


if __name__ == "__main__":
    core.main()
