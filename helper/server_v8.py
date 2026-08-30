from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import server_v7 as v7

core = v7.core
core.VERSION = "0.5.8"


def is_hls_url(value: str) -> bool:
    return str(value or "").lower().split("?", 1)[0].split("#", 1)[0].endswith(".m3u8")


def _quality_p(width: int, height: int) -> int:
    if width > 0 and height > 0:
        return min(width, height)
    return height or width or 0


def _headers_for(payload: dict) -> dict[str, str]:
    headers = v7.v5.v4.sanitize_request_headers(payload.get("request_headers"))
    page_url = str(payload.get("page_url") or "").strip()
    if page_url and not headers.get("Referer"):
        headers["Referer"] = page_url
    return headers


def _add_input_headers(cmd: list[str], headers: dict[str, str], page_url: str) -> None:
    user_agent = headers.get("User-Agent")
    referer = headers.get("Referer") or page_url
    extra_headers = v7.v5.header_block(headers)
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referer:
        cmd += ["-referer", referer]
    if extra_headers:
        cmd += ["-headers", extra_headers]


def _run_ffmpeg(cmd: list[str], timeout: int = 1800) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "FFmpeg HLS download failed").strip()
    return False, detail[-2600:]


def _selected_hls(payload: dict, headers: dict[str, str]) -> dict:
    media_url = str(payload.get("media_url") or "").strip()
    v7._set_payload_dimensions(payload)
    try:
        try:
            selected = v7.select_best_hls_v7(media_url, headers)
        except Exception:
            width = int(payload.get("playback_width") or 0)
            height = int(payload.get("playback_height") or 0)
            selected = {
                "video_url": media_url,
                "audio_url": "",
                "width": width,
                "height": height,
                "bandwidth": 0,
                "is_master": False,
                "quality_p": _quality_p(width, height),
                "resolution_source": "browser-player" if width and height else "unknown",
            }
        return selected
    finally:
        v7._clear_payload_dimensions()


def probe_media_v8(payload: dict) -> dict:
    media_url = str(payload.get("media_url") or "").strip()
    if not is_hls_url(media_url):
        result = v7.probe_media_v7(payload)
        result["version"] = core.VERSION
        return result

    headers = _headers_for(payload)
    selected = _selected_hls(payload, headers)
    width = int(selected.get("width") or 0)
    height = int(selected.get("height") or 0)
    quality_p = int(selected.get("quality_p") or _quality_p(width, height))
    highest = v7.v5.synthetic_best_format(selected)
    highest["width"] = width
    highest["height"] = height
    highest["quality_p"] = quality_p
    highest["format_note"] = "OmniFetch direct HLS"

    return {
        "ok": True,
        "version": core.VERSION,
        "platform": core.detect_platform(str(payload.get("page_url") or media_url)),
        "strategy": "direct-hls-no-extractor",
        "title": str(payload.get("title") or "视频"),
        "duration": None,
        "thumbnail": "",
        "is_live": False,
        "formats": [highest],
        "highest_hls": {
            "width": width,
            "height": height,
            "quality_p": quality_p,
            "bandwidth": int(selected.get("bandwidth") or 0),
            "is_master": bool(selected.get("is_master")),
            "resolution_source": selected.get("resolution_source") or "",
        },
    }


def run_direct_hls_video(job_id: str, payload: dict) -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    ffmpeg = v7.v5.ffmpeg_executable()
    if not media_url or not ffmpeg:
        core.update_job(job_id, status="failed", error="M3U8 直连下载需要 FFmpeg。")
        return False

    headers = _headers_for(payload)
    page_url = str(payload.get("page_url") or "").strip()
    selected = _selected_hls(payload, headers)
    width = int(selected.get("width") or 0)
    height = int(selected.get("height") or 0)
    quality_p = int(selected.get("quality_p") or _quality_p(width, height))

    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-direct-hls"
    v7.v5.v4.v3.safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[tuple[str, list[str], Path]] = []

    explicit_video = str(selected.get("video_url") or "").strip()
    explicit_audio = str(selected.get("audio_url") or "").strip()
    if explicit_video and explicit_video != media_url:
        output = temp_dir / "explicit.mp4"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        _add_input_headers(cmd, headers, page_url)
        cmd += ["-i", explicit_video]
        if explicit_audio:
            _add_input_headers(cmd, headers, page_url)
            cmd += ["-i", explicit_audio, "-map", "0:v:0?", "-map", "1:a:0?"]
        else:
            cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(output)]
        attempts.append(("direct-explicit-highest-hls", cmd, output))

    # Important: use the exact captured M3U8 as a second direct strategy.
    # No yt-dlp/generic extractor is involved. FFmpeg's automatic stream
    # selection chooses the highest-resolution video stream from a master HLS.
    output = temp_dir / "captured-master.mp4"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    _add_input_headers(cmd, headers, page_url)
    cmd += ["-i", media_url, "-c", "copy", "-movflags", "+faststart", str(output)]
    attempts.append(("direct-captured-m3u8", cmd, output))

    core.update_job(
        job_id,
        status="downloading",
        percent=None,
        strategy=attempts[0][0],
        target_url=media_url,
        selected_width=width,
        selected_height=height,
        selected_quality_p=quality_p,
        selected_quality=f"{width}x{height}" if width and height else "best available",
        error=None,
    )

    errors: list[str] = []
    for mode, cmd, output in attempts:
        if output.exists():
            try:
                output.unlink()
            except Exception:
                pass
        core.update_job(job_id, status="downloading", strategy=mode, target_url=media_url, error=None)
        ok, error = _run_ffmpeg(cmd)
        size = output.stat().st_size if output.exists() else 0
        if ok and size >= v7.v5.v4.v3.MIN_VIDEO_BYTES:
            core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            title = v7.v5.safe_title(payload.get("title") or "video")
            suffix = f" [{quality_p}p]" if quality_p else ""
            destination = v7.v5.v4.v3.unique_destination(core.DOWNLOAD_DIR / f"{title}{suffix}.mp4")
            shutil.move(str(output), str(destination))
            v7.v5.v4.v3.safe_rmtree(temp_dir)
            core.update_job(
                job_id,
                status="completed",
                percent=100,
                title=title,
                output_dir=str(core.DOWNLOAD_DIR),
                output_file=str(destination),
                output_bytes=destination.stat().st_size,
                download_kind="video",
                strategy=mode,
                selected_width=width,
                selected_height=height,
                selected_quality_p=quality_p,
                selected_quality=f"{width}x{height}" if width and height else "best available",
                error=None,
            )
            return True
        errors.append(f"{mode}: {error or f'结果仅 {size} 字节'}")

    v7.v5.v4.v3.safe_rmtree(temp_dir)
    core.update_job(
        job_id,
        status="failed",
        strategy="direct-hls-no-extractor",
        error="M3U8 直连下载失败。" + " | ".join(errors),
    )
    return False


def run_direct_hls_audio(job_id: str, payload: dict) -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    ffmpeg = v7.v5.ffmpeg_executable()
    if not media_url or not ffmpeg:
        core.update_job(job_id, status="failed", error="M3U8 音频提取需要 FFmpeg。")
        return False

    headers = _headers_for(payload)
    page_url = str(payload.get("page_url") or "").strip()
    selected = _selected_hls(payload, headers)
    source = str(selected.get("audio_url") or selected.get("video_url") or media_url)
    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-direct-audio"
    v7.v5.v4.v3.safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    for transcode in (False, True):
        output = temp_dir / ("audio-aac.m4a" if transcode else "audio-copy.m4a")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        _add_input_headers(cmd, headers, page_url)
        cmd += ["-i", source, "-vn"]
        if transcode:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-c:a", "copy"]
        cmd += [str(output)]
        mode = "direct-hls-audio-aac" if transcode else "direct-hls-audio-copy"
        core.update_job(job_id, status="downloading", strategy=mode, target_url=source, error=None)
        ok, error = _run_ffmpeg(cmd)
        size = output.stat().st_size if output.exists() else 0
        if ok and size >= 4096:
            core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            title = v7.v5.safe_title(payload.get("title") or "audio")
            destination = v7.v5.v4.v3.unique_destination(core.DOWNLOAD_DIR / f"{title} [audio].m4a")
            shutil.move(str(output), str(destination))
            v7.v5.v4.v3.safe_rmtree(temp_dir)
            core.update_job(
                job_id,
                status="completed",
                percent=100,
                title=title,
                output_dir=str(core.DOWNLOAD_DIR),
                output_file=str(destination),
                output_bytes=destination.stat().st_size,
                download_kind="audio",
                strategy=mode,
                error=None,
            )
            return True
        errors.append(f"{mode}: {error or f'结果仅 {size} 字节'}")

    v7.v5.v4.v3.safe_rmtree(temp_dir)
    core.update_job(job_id, status="failed", strategy="direct-hls-audio", error="M3U8 音频提取失败。" + " | ".join(errors))
    return False


def run_download_v8(job_id: str, payload: dict) -> None:
    media_url = str(payload.get("media_url") or "").strip()
    kind = str(payload.get("download_kind") or "video").strip().lower()

    # Captured HLS is authoritative. Never send it through yt-dlp's generic
    # webpage extractor; this avoids Cloudflare webpage challenges after the
    # browser has already exposed the real playable M3U8.
    if is_hls_url(media_url):
        if kind == "audio":
            run_direct_hls_audio(job_id, payload)
        else:
            run_direct_hls_video(job_id, payload)
        return

    v7.run_download_v7(job_id, payload)


core.probe_media = probe_media_v8
core.run_download = run_download_v8


if __name__ == "__main__":
    core.main()
