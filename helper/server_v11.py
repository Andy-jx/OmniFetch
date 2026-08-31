from __future__ import annotations

import concurrent.futures
import math
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import server_v10 as v10

core = v10.core
core.VERSION = "0.6.0"

v9 = v10.v9
v8 = v9.v8
v7 = v8.v7
v5 = v7.v5
v4 = v5.v4
v3 = v4.v3

DIRECT_VIDEO_TYPES = {"mp4", "webm", "mov", "m4v", "flv", "video"}
AUDIO_TYPES = {"mp3", "m4a", "aac", "audio"}
STREAM_TYPES = {"hls", "dash"}
MAX_CANDIDATES = 20
MAX_DIRECT_PROBES = 8
DIRECT_MIN_BYTES = 64 * 1024

_base_sanitize_headers = v4.sanitize_request_headers
_EXTRA_HEADERS = {
    "sec-fetch-site": "Sec-Fetch-Site",
    "sec-fetch-mode": "Sec-Fetch-Mode",
    "sec-fetch-dest": "Sec-Fetch-Dest",
    "sec-ch-ua": "Sec-CH-UA",
    "sec-ch-ua-mobile": "Sec-CH-UA-Mobile",
    "sec-ch-ua-platform": "Sec-CH-UA-Platform",
    "cache-control": "Cache-Control",
    "pragma": "Pragma",
}


def sanitize_request_headers_v11(raw) -> dict[str, str]:
    result = _base_sanitize_headers(raw)
    if not isinstance(raw, dict):
        return result
    total = sum(len(str(value or "")) for value in result.values())
    for key, value in raw.items():
        lower = str(key or "").strip().lower()
        canonical = _EXTRA_HEADERS.get(lower)
        if not canonical:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        text = text[:4096]
        total += len(text)
        if total > 61440:
            break
        result[canonical] = text
    # Never reuse a browser Range header for a different media request. Direct
    # downloads generate their own range from byte zero, and HLS pieces must be
    # free to request their own object/range.
    result.pop("Range", None)
    return result


# Upgrade the shared sanitizer in-place so HLS, FFmpeg and generic fallbacks all
# receive the richer browser request context without changing every old module.
v4.sanitize_request_headers = sanitize_request_headers_v11


def _clean_path(url: str) -> str:
    return str(url or "").lower().split("?", 1)[0].split("#", 1)[0]


def _is_hls(url: str, kind: str = "") -> bool:
    return kind == "hls" or v8.is_hls_url(url)


def _is_dash(url: str, kind: str = "") -> bool:
    return kind == "dash" or _clean_path(url).endswith(".mpd")


def _guess_type(url: str, declared: str = "") -> str:
    value = str(declared or "").lower().strip()
    if value:
        return value
    path = _clean_path(url)
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    if path.endswith(".mp4"):
        return "mp4"
    if path.endswith(".webm"):
        return "webm"
    if path.endswith(".mov"):
        return "mov"
    if path.endswith(".m4v"):
        return "m4v"
    if path.endswith(".flv"):
        return "flv"
    if path.endswith(".m4a"):
        return "m4a"
    if path.endswith(".mp3"):
        return "mp3"
    if path.endswith(".aac"):
        return "aac"
    return "video"


def _safe_url(value: str) -> str:
    try:
        return core.safe_http_url(str(value or "").strip())
    except Exception:
        return ""


def _candidate_headers(raw) -> dict[str, str]:
    return sanitize_request_headers_v11(raw if isinstance(raw, dict) else {})


def _normalize_candidates(payload: dict) -> list[dict]:
    combined: list[dict] = []
    primary = _safe_url(payload.get("media_url") or "")
    if primary:
        combined.append({
            "url": primary,
            "type": _guess_type(primary),
            "score": 10000,
            "contentLength": 0,
            "requestHeaders": _candidate_headers(payload.get("request_headers")),
            "playbackWidth": int(payload.get("playback_width") or 0),
            "playbackHeight": int(payload.get("playback_height") or 0),
            "playbackDuration": float(payload.get("playback_duration") or 0),
            "detectedAt": 0,
        })

    raw_candidates = payload.get("media_candidates") or []
    if isinstance(raw_candidates, list):
        for item in raw_candidates[:MAX_CANDIDATES]:
            if not isinstance(item, dict):
                continue
            url = _safe_url(item.get("url") or "")
            if not url:
                continue
            combined.append({
                "url": url,
                "type": _guess_type(url, item.get("type") or ""),
                "score": float(item.get("score") or 0),
                "contentLength": int(item.get("contentLength") or item.get("content_length") or 0),
                "requestHeaders": _candidate_headers(item.get("requestHeaders") or item.get("request_headers") or {}),
                "playbackWidth": int(item.get("playbackWidth") or item.get("playback_width") or payload.get("playback_width") or 0),
                "playbackHeight": int(item.get("playbackHeight") or item.get("playback_height") or payload.get("playback_height") or 0),
                "playbackDuration": float(item.get("playbackDuration") or item.get("playback_duration") or payload.get("playback_duration") or 0),
                "detectedAt": int(item.get("detectedAt") or item.get("detected_at") or 0),
            })

    fallbacks = payload.get("fallback_media_urls") or []
    if isinstance(fallbacks, list):
        for raw in fallbacks[:12]:
            url = _safe_url(raw)
            if url:
                combined.append({
                    "url": url,
                    "type": _guess_type(url),
                    "score": 0,
                    "contentLength": 0,
                    "requestHeaders": {},
                    "playbackWidth": int(payload.get("playback_width") or 0),
                    "playbackHeight": int(payload.get("playback_height") or 0),
                    "playbackDuration": float(payload.get("playback_duration") or 0),
                    "detectedAt": 0,
                })

    dedup: dict[str, dict] = {}
    for item in combined:
        url = item["url"]
        current = dedup.get(url)
        if not current:
            dedup[url] = item
            continue
        if item["score"] > current["score"]:
            current["score"] = item["score"]
        current["contentLength"] = max(current["contentLength"], item["contentLength"])
        current["detectedAt"] = max(current["detectedAt"], item["detectedAt"])
        current["playbackWidth"] = current["playbackWidth"] or item["playbackWidth"]
        current["playbackHeight"] = current["playbackHeight"] or item["playbackHeight"]
        current["playbackDuration"] = current["playbackDuration"] or item["playbackDuration"]
        current["requestHeaders"].update(item["requestHeaders"])
    return list(dedup.values())[:MAX_CANDIDATES]


def _payload_for_candidate(payload: dict, candidate: dict) -> dict:
    copied = dict(payload)
    copied["media_url"] = candidate["url"]
    copied["request_headers"] = candidate.get("requestHeaders") or payload.get("request_headers") or {}
    copied["playback_width"] = int(candidate.get("playbackWidth") or payload.get("playback_width") or 0)
    copied["playback_height"] = int(candidate.get("playbackHeight") or payload.get("playback_height") or 0)
    copied["playback_duration"] = float(candidate.get("playbackDuration") or payload.get("playback_duration") or 0)
    return copied


def _quality_p(width: int, height: int) -> int:
    if width > 0 and height > 0:
        return min(width, height)
    return height or width or 0


def _url_quality_hint(url: str) -> int:
    lower = str(url or "").lower()
    values = [int(value) for value in re.findall(r"(?<!\d)(2160|1440|1080|720|540|480|360|270)p?(?!\d)", lower)]
    for width, height in re.findall(r"(?<!\d)(\d{3,4})[xX](\d{3,4})(?!\d)", lower):
        values.append(min(int(width), int(height)))
    return max(values or [0])


def _duration_from_text(text: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text or "")
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _dimensions_from_text(text: str) -> tuple[int, int]:
    for line in str(text or "").splitlines():
        if "Video:" not in line:
            continue
        for match in re.finditer(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", line):
            width, height = int(match.group(1)), int(match.group(2))
            if 80 <= width <= 16384 and 80 <= height <= 16384:
                return width, height
    return 0, 0


def _remote_probe(candidate: dict, payload: dict) -> dict:
    url = candidate["url"]
    cp = _payload_for_candidate(payload, candidate)
    ffmpeg = v5.ffmpeg_executable()
    result = {"width": 0, "height": 0, "duration": 0.0, "has_audio": False, "probe_ok": False}
    if not ffmpeg:
        return result
    headers = v8._headers_for(cp)
    page_url = str(cp.get("page_url") or "").strip()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-rw_timeout", "10000000"]
    v8._add_input_headers(cmd, headers, page_url)
    cmd += [
        "-probesize", "5000000",
        "-analyzeduration", "5000000",
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
            timeout=14,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return result
    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    width, height = _dimensions_from_text(text)
    result.update({
        "width": width,
        "height": height,
        "duration": _duration_from_text(text),
        "has_audio": "Audio:" in text,
        "probe_ok": bool(width and height),
    })
    return result


def _direct_rank_tuple(candidate: dict) -> tuple:
    meta = candidate.get("probe") or {}
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    q = _quality_p(width, height) or _url_quality_hint(candidate["url"])
    area = width * height
    size = int(candidate.get("contentLength") or 0)
    return (
        q,
        area,
        int(meta.get("probe_ok") or 0),
        int(math.log2(max(1, size))),
        float(candidate.get("score") or 0),
        int(candidate.get("detectedAt") or 0),
    )


def _rank_direct_candidates(candidates: list[dict], payload: dict) -> list[dict]:
    pool = [item for item in candidates if item.get("type") in DIRECT_VIDEO_TYPES and not _is_hls(item["url"], item.get("type", "")) and not _is_dash(item["url"], item.get("type", ""))]
    pool.sort(key=lambda item: (float(item.get("score") or 0), int(item.get("contentLength") or 0), int(item.get("detectedAt") or 0)), reverse=True)
    probe_pool = pool[:MAX_DIRECT_PROBES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, max(1, len(probe_pool)))) as executor:
        futures = {executor.submit(_remote_probe, item, payload): item for item in probe_pool}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                item["probe"] = future.result()
            except Exception:
                item["probe"] = {}
    return sorted(pool, key=_direct_rank_tuple, reverse=True)


def _parse_content_range(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", str(value or "").strip(), re.I)
    if not match or match.group(3) == "*":
        return None
    start, end, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if end < start or total <= 0:
        return None
    return start, end, total


def _looks_like_text(head: bytes, content_type: str) -> bool:
    ctype = str(content_type or "").lower()
    sample = bytes(head or b"")[:1024].lstrip().lower()
    return (
        "text/html" in ctype
        or "application/json" in ctype
        or sample.startswith(b"<!doctype html")
        or sample.startswith(b"<html")
        or sample.startswith(b"{\"error\"")
    )


def _download_http_file(job_id: str, candidate: dict, payload: dict, destination: Path) -> tuple[int, int | None]:
    cp = _payload_for_candidate(payload, candidate)
    url = candidate["url"]
    errors: list[str] = []
    profiles = v9._header_profiles(cp, url)

    for profile in profiles:
        for use_range in (True, False):
            try:
                destination.unlink(missing_ok=True)
                downloaded = 0
                total: int | None = None
                next_offset = 0
                first_request = True
                while True:
                    headers = dict(profile)
                    headers.pop("Range", None)
                    headers.setdefault("Accept-Encoding", "identity")
                    if use_range:
                        headers["Range"] = f"bytes={next_offset}-"
                    req = Request(url, headers=headers, method="GET")
                    with urlopen(req, timeout=45) as response:
                        status = int(getattr(response, "status", 200) or 200)
                        ctype = str(response.headers.get("Content-Type") or "")
                        content_range = _parse_content_range(response.headers.get("Content-Range") or "")
                        content_length = int(response.headers.get("Content-Length") or 0)
                        if content_range:
                            _, _, total = content_range
                        elif status == 200 and content_length > 0:
                            total = content_length

                        mode = "wb" if first_request else "ab"
                        with destination.open(mode) as output:
                            head = response.read(2048)
                            if first_request and _looks_like_text(head, ctype):
                                raise RuntimeError(f"服务器返回 {ctype or 'HTML/JSON'}，不是视频")
                            output.write(head)
                            downloaded += len(head)
                            while True:
                                chunk = response.read(1024 * 1024)
                                if not chunk:
                                    break
                                output.write(chunk)
                                downloaded += len(chunk)
                                if total:
                                    core.update_job(job_id, status="downloading", percent=round(min(88.0, downloaded / total * 88), 1), downloaded_bytes=downloaded, total_bytes=total)
                                else:
                                    core.update_job(job_id, status="downloading", downloaded_bytes=downloaded)

                    if not use_range or status == 200 or not total or downloaded >= total:
                        break
                    if content_range is None:
                        break
                    next_offset = downloaded
                    first_request = False

                if downloaded < DIRECT_MIN_BYTES:
                    raise RuntimeError(f"直连结果仅 {downloaded} 字节")
                if total and downloaded < total * 0.98:
                    raise RuntimeError(f"直连未下载完整：{downloaded}/{total} 字节")
                return downloaded, total
            except Exception as exc:
                errors.append(str(exc))
                destination.unlink(missing_ok=True)
    raise RuntimeError(" / ".join(errors[-4:]) or "直连请求失败")


def _probe_local(path: Path) -> dict:
    ffmpeg = v5.ffmpeg_executable()
    result = {"width": 0, "height": 0, "duration": 0.0, "has_audio": False}
    if not ffmpeg or not path.exists():
        return result
    cmd = [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return result
    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    width, height = _dimensions_from_text(text)
    result.update({"width": width, "height": height, "duration": _duration_from_text(text), "has_audio": "Audio:" in text})
    return result


def _direct_valid(meta: dict, expected_duration: float) -> bool:
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    duration = float(meta.get("duration") or 0)
    if width <= 0 or height <= 0 or duration <= 0.2:
        return False
    if expected_duration > 2.0 and duration < expected_duration * 0.70:
        return False
    return True


def _remux_direct(source: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = v5.ffmpeg_executable()
    if not ffmpeg:
        return False, "FFmpeg not found"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", str(source),
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(output),
    ]
    return v8._run_ffmpeg(cmd, timeout=1800)


def _run_direct_candidate(job_id: str, payload: dict, candidate: dict, attempt: int, attempt_total: int) -> tuple[bool, str]:
    cp = _payload_for_candidate(payload, candidate)
    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-direct-v11"
    v3.safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    raw = temp_dir / "source.bin"
    output = temp_dir / "output.mp4"
    probe = candidate.get("probe") or {}
    expected_duration = float(candidate.get("playbackDuration") or cp.get("playback_duration") or probe.get("duration") or 0)

    core.update_job(
        job_id,
        status="downloading",
        percent=0,
        strategy="captured-direct-v11",
        mode="captured-direct",
        target_url=candidate["url"],
        attempt=attempt,
        attempt_total=attempt_total,
        selected_width=int(probe.get("width") or candidate.get("playbackWidth") or 0),
        selected_height=int(probe.get("height") or candidate.get("playbackHeight") or 0),
        expected_duration=round(expected_duration, 3) if expected_duration else None,
        error=None,
    )

    try:
        downloaded, total = _download_http_file(job_id, candidate, payload, raw)
        core.update_job(job_id, status="processing", percent=91, downloaded_bytes=downloaded, total_bytes=total)
        raw_meta = _probe_local(raw)
        ok, error = _remux_direct(raw, output)
        output_meta = _probe_local(output) if ok and output.exists() else {}

        final_source: Path | None = None
        final_meta: dict = {}
        if ok and output.exists() and output.stat().st_size >= DIRECT_MIN_BYTES and _direct_valid(output_meta, expected_duration):
            final_source, final_meta = output, output_meta
        elif raw.exists() and raw.stat().st_size >= DIRECT_MIN_BYTES and _direct_valid(raw_meta, expected_duration):
            # Some already-valid MP4 files do not need a second remux. Keep them
            # only after duration/dimensions validation.
            kind = v9._container_kind(raw)
            if kind == "mp4":
                final_source, final_meta = raw, raw_meta

        if final_source is None:
            actual = float((output_meta or raw_meta).get("duration") or 0)
            raise RuntimeError(error or f"下载文件校验失败：时长 {actual:.2f}s，预期约 {expected_duration:.2f}s")

        width = int(final_meta.get("width") or 0)
        height = int(final_meta.get("height") or 0)
        quality_p = _quality_p(width, height)
        core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        title = v5.safe_title(cp.get("title") or "video")
        suffix = f" [{quality_p}p]" if quality_p else ""
        destination = v3.unique_destination(core.DOWNLOAD_DIR / f"{title}{suffix}.mp4")
        shutil.move(str(final_source), str(destination))
        v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="completed",
            percent=100,
            title=title,
            output_dir=str(core.DOWNLOAD_DIR),
            output_file=str(destination),
            output_bytes=destination.stat().st_size,
            output_duration=round(float(final_meta.get("duration") or 0), 3),
            expected_duration=round(expected_duration, 3) if expected_duration else None,
            download_kind="video",
            strategy="captured-direct-v11",
            selected_width=width,
            selected_height=height,
            selected_quality_p=quality_p,
            selected_quality=f"{width}x{height}" if width and height else "best available",
            downloaded_bytes=downloaded,
            total_bytes=total,
            error=None,
        )
        return True, ""
    except Exception as exc:
        v3.safe_rmtree(temp_dir)
        return False, str(exc)


def _stream_bonus(candidate: dict) -> int:
    url = str(candidate.get("url") or "").lower()
    bonus = 0
    if "master.m3u8" in url:
        bonus += 1000
    elif "manifest.m3u8" in url:
        bonus += 850
    elif "master" in url:
        bonus += 500
    return bonus


def _run_hls_matrix(job_id: str, payload: dict, candidates: list[dict]) -> bool:
    hls = [item for item in candidates if _is_hls(item["url"], item.get("type", ""))]
    hls.sort(key=lambda item: (_stream_bonus(item) + float(item.get("score") or 0), int(item.get("detectedAt") or 0)), reverse=True)
    if not hls:
        return False
    errors: list[str] = []
    for index, candidate in enumerate(hls[:8], start=1):
        cp = _payload_for_candidate(payload, candidate)
        core.update_job(job_id, status="resolving", strategy="hls-candidate-matrix", attempt=index, attempt_total=min(8, len(hls)), target_url=candidate["url"], error=None)
        if v10.run_native_hls_video_v10(job_id, cp):
            return True
        with core.JOBS_LOCK:
            job = dict(core.JOBS.get(job_id) or {})
        errors.append(str(job.get("error") or "HLS candidate failed"))
        if index < min(8, len(hls)):
            core.update_job(job_id, status="retrying", error=errors[-1], next_attempt=index + 1)
    core.update_job(job_id, status="failed", strategy="hls-candidate-matrix", error="多个已捕获 M3U8 均失败：" + " | ".join(errors[-4:]))
    return False


def _run_direct_matrix(job_id: str, payload: dict, candidates: list[dict]) -> bool:
    ranked = _rank_direct_candidates(candidates, payload)
    if not ranked:
        return False
    errors: list[str] = []
    attempts = ranked[:8]
    for index, candidate in enumerate(attempts, start=1):
        ok, error = _run_direct_candidate(job_id, payload, candidate, index, len(attempts))
        if ok:
            return True
        errors.append(f"{urlparse(candidate['url']).hostname or 'media'}: {error}")
        if index < len(attempts):
            core.update_job(job_id, status="retrying", strategy="captured-direct-v11", error=errors[-1], next_attempt=index + 1, attempt_total=len(attempts))
    core.update_job(job_id, status="retrying", strategy="captured-direct-v11", error="已捕获直连均未通过完整性校验：" + " | ".join(errors[-4:]))
    return False


def probe_media_v11(payload: dict) -> dict:
    candidates = _normalize_candidates(payload)
    hls = [item for item in candidates if _is_hls(item["url"], item.get("type", ""))]
    if hls:
        cp = _payload_for_candidate(payload, sorted(hls, key=lambda item: _stream_bonus(item) + float(item.get("score") or 0), reverse=True)[0])
        result = v10.v9.v8.probe_media_v8(cp)
        result["version"] = core.VERSION
        result["candidate_count"] = len(candidates)
        return result

    direct = _rank_direct_candidates(candidates, payload)
    if direct:
        best = direct[0]
        meta = best.get("probe") or {}
        width = int(meta.get("width") or best.get("playbackWidth") or 0)
        height = int(meta.get("height") or best.get("playbackHeight") or 0)
        return {
            "ok": True,
            "version": core.VERSION,
            "platform": core.detect_platform(str(payload.get("page_url") or best["url"])),
            "strategy": "captured-direct-probe-v11",
            "title": str(payload.get("title") or "视频"),
            "duration": float(meta.get("duration") or best.get("playbackDuration") or 0) or None,
            "thumbnail": "",
            "is_live": False,
            "candidate_count": len(candidates),
            "formats": [{
                "format_id": "omnifetch_direct_best",
                "ext": "mp4",
                "height": height,
                "width": width,
                "fps": None,
                "tbr": None,
                "filesize": int(best.get("contentLength") or 0),
                "protocol": "https",
                "format_note": "OmniFetch captured direct media",
                "vcodec": "unknown",
                "acodec": "unknown",
                "has_video": True,
                "has_audio": bool(meta.get("has_audio")),
                "quality_p": _quality_p(width, height),
            }],
        }

    result = v10.v9.v8.v7.probe_media_v7(payload)
    result["version"] = core.VERSION
    result["candidate_count"] = len(candidates)
    return result


def run_download_v11(job_id: str, payload: dict) -> None:
    kind = str(payload.get("download_kind") or "video").strip().lower()
    candidates = _normalize_candidates(payload)

    if kind == "video":
        if any(_is_hls(item["url"], item.get("type", "")) for item in candidates):
            if _run_hls_matrix(job_id, payload, candidates):
                return
            # A failed HLS matrix is authoritative for captured stream pages;
            # do not silently replace it with a webpage extractor that may hit
            # Cloudflare or produce a different/low-quality file.
            return

        if _run_direct_matrix(job_id, payload, candidates):
            return

        # Last resort for pages where the browser exposes no usable direct
        # media, such as sites that need a dedicated extractor. Old behavior is
        # retained only after captured media has been exhausted and validated.
        v10.run_download_v10(job_id, payload)
        return

    # Audio keeps the mature v10 path. If the chosen source is HLS, v8 extracts
    # its best audio directly without using generic webpage parsing.
    v10.run_download_v10(job_id, payload)


core.probe_media = probe_media_v11
core.run_download = run_download_v11


if __name__ == "__main__":
    core.main()
