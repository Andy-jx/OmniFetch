from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import server_v4 as v4

core = v4.core
core.VERSION = "0.5.5"

_tls = threading.local()


def parse_attr_list(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', text or "", re.I):
        key = match.group(1).upper()
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        attrs[key] = value
    return attrs


def fetch_text(url: str, headers: dict[str, str]) -> str:
    request_headers = dict(headers or {})
    request_headers.setdefault("Accept", "application/vnd.apple.mpegurl, application/x-mpegURL, */*")
    req = Request(url, headers=request_headers, method="GET")
    with urlopen(req, timeout=15) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError("HLS master playlist is unexpectedly large")
    return data.decode("utf-8", errors="replace")


def safe_join(base_url: str, child: str) -> str:
    return core.safe_http_url(urljoin(base_url, child.strip()))


def resolution_from(attrs: dict[str, str]) -> tuple[int, int]:
    value = attrs.get("RESOLUTION", "")
    match = re.fullmatch(r"(\d+)x(\d+)", value, re.I)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def select_best_hls(media_url: str, headers: dict[str, str]) -> dict:
    text = fetch_text(media_url, headers)
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]

    audio_groups: dict[str, list[dict]] = {}
    for line in lines:
        if not line.upper().startswith("#EXT-X-MEDIA:"):
            continue
        attrs = parse_attr_list(line.split(":", 1)[1])
        if attrs.get("TYPE", "").upper() != "AUDIO" or not attrs.get("URI"):
            continue
        group = attrs.get("GROUP-ID", "")
        if not group:
            continue
        audio_groups.setdefault(group, []).append(attrs)

    variants: list[dict] = []
    for index, line in enumerate(lines):
        if not line.upper().startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = parse_attr_list(line.split(":", 1)[1])
        uri = ""
        for next_line in lines[index + 1 :]:
            if not next_line:
                continue
            if next_line.startswith("#"):
                continue
            uri = next_line
            break
        if not uri:
            continue

        width, height = resolution_from(attrs)
        bandwidth = int(attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or 0)
        variants.append(
            {
                "url": safe_join(media_url, uri),
                "width": width,
                "height": height,
                "bandwidth": bandwidth,
                "audio_group": attrs.get("AUDIO", ""),
                "codecs": attrs.get("CODECS", ""),
            }
        )

    if not variants:
        return {
            "video_url": media_url,
            "audio_url": "",
            "width": 0,
            "height": 0,
            "bandwidth": 0,
            "is_master": False,
        }

    variants.sort(
        key=lambda item: (
            int(item["width"] or 0) * int(item["height"] or 0),
            int(item["height"] or 0),
            int(item["bandwidth"] or 0),
        ),
        reverse=True,
    )
    best = variants[0]

    audio_url = ""
    group = best.get("audio_group") or ""
    candidates = audio_groups.get(group, []) if group else []
    if candidates:
        candidates.sort(
            key=lambda attrs: (
                1 if attrs.get("DEFAULT", "").upper() == "YES" else 0,
                1 if attrs.get("AUTOSELECT", "").upper() == "YES" else 0,
            ),
            reverse=True,
        )
        audio_url = safe_join(media_url, candidates[0]["URI"])

    return {
        "video_url": best["url"],
        "audio_url": audio_url,
        "width": best["width"],
        "height": best["height"],
        "bandwidth": best["bandwidth"],
        "is_master": True,
    }


def ffmpeg_executable() -> str | None:
    folder = core.find_ffmpeg()
    if folder:
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidate = Path(folder) / name
            if candidate.exists():
                return str(candidate)
    return shutil.which("ffmpeg")


def header_block(headers: dict[str, str]) -> str:
    skip = {"User-Agent", "Referer"}
    lines = [f"{key}: {value}" for key, value in headers.items() if key not in skip and value]
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def safe_title(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip().strip(".")
    return text[:140] or "video"


def run_ffmpeg_best_hls(job_id: str, payload: dict) -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    if not media_url or not media_url.lower().split("?", 1)[0].endswith(".m3u8"):
        return False

    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return False

    headers = v4.sanitize_request_headers(payload.get("request_headers"))
    page_url = str(payload.get("page_url") or "").strip()
    if page_url and not headers.get("Referer"):
        headers["Referer"] = page_url

    try:
        selected = select_best_hls(media_url, headers)
    except Exception as exc:
        core.update_job(job_id, status="retrying", error=f"最高画质播放清单解析失败：{exc}")
        return False

    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-best-hls"
    v4.v3.safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_output = temp_dir / "stream.mp4"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    user_agent = headers.get("User-Agent")
    referer = headers.get("Referer") or page_url
    extra_headers = header_block(headers)
    if user_agent:
        cmd += ["-user_agent", user_agent]
    if referer:
        cmd += ["-referer", referer]
    if extra_headers:
        cmd += ["-headers", extra_headers]

    cmd += ["-i", selected["video_url"]]
    if selected.get("audio_url"):
        cmd += ["-i", selected["audio_url"], "-map", "0:v:0?", "-map", "1:a:0?"]
    else:
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(temp_output)]

    quality = f"{selected['width']}x{selected['height']}" if selected.get("height") else "best available"
    core.update_job(
        job_id,
        status="downloading",
        percent=None,
        strategy="ffmpeg-explicit-highest-hls",
        target_url=selected["video_url"],
        selected_quality=quality,
        selected_width=selected.get("width") or 0,
        selected_height=selected.get("height") or 0,
        selected_bandwidth=selected.get("bandwidth") or 0,
        selected_audio_url=selected.get("audio_url") or "",
        error=None,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "FFmpeg HLS download failed").strip()[-2200:]
            raise RuntimeError(detail)
        if not temp_output.exists() or temp_output.stat().st_size < v4.v3.MIN_VIDEO_BYTES:
            size = temp_output.stat().st_size if temp_output.exists() else 0
            raise RuntimeError(f"最高画质 HLS 下载结果异常，仅 {size} 字节")

        core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        title = safe_title(payload.get("title") or "video")
        suffix = f" [{selected['height']}p]" if selected.get("height") else ""
        destination = v4.v3.unique_destination(core.DOWNLOAD_DIR / f"{title}{suffix}.mp4")
        shutil.move(str(temp_output), str(destination))
        v4.v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="completed",
            percent=100,
            title=title,
            output_dir=str(core.DOWNLOAD_DIR),
            output_file=str(destination),
            output_bytes=destination.stat().st_size,
            download_kind="video",
            strategy="ffmpeg-explicit-highest-hls",
            selected_quality=quality,
            selected_width=selected.get("width") or 0,
            selected_height=selected.get("height") or 0,
            error=None,
        )
        return True
    except Exception as exc:
        v4.v3.safe_rmtree(temp_dir)
        core.update_job(job_id, status="retrying", error=f"最高画质 HLS 直取失败：{exc}")
        return False


def synthetic_best_format(selected: dict) -> dict:
    return {
        "format_id": "omnifetch_hls_highest",
        "ext": "mp4",
        "height": int(selected.get("height") or 0),
        "width": int(selected.get("width") or 0),
        "fps": None,
        "tbr": round(float(selected.get("bandwidth") or 0) / 1000, 1),
        "filesize": 0,
        "protocol": "m3u8",
        "format_note": "OmniFetch explicit highest HLS variant",
        "vcodec": "unknown",
        "acodec": "unknown" if not selected.get("audio_url") else "external",
        "has_video": True,
        "has_audio": not bool(selected.get("audio_url")),
    }


def probe_media_v5(payload: dict) -> dict:
    media_url = str(payload.get("media_url") or "").strip()
    headers = v4.sanitize_request_headers(payload.get("request_headers"))
    selected = None
    if media_url.lower().split("?", 1)[0].endswith(".m3u8"):
        try:
            selected = select_best_hls(media_url, headers)
        except Exception:
            selected = None

    try:
        result = v4.probe_media_v4(payload)
    except Exception:
        if not selected:
            raise
        result = {
            "ok": True,
            "version": core.VERSION,
            "platform": core.detect_platform(str(payload.get("page_url") or media_url)),
            "strategy": "hls-master-parser",
            "title": str(payload.get("title") or "视频"),
            "duration": None,
            "thumbnail": "",
            "is_live": False,
            "formats": [],
        }

    result["version"] = core.VERSION
    if selected and selected.get("is_master"):
        highest = synthetic_best_format(selected)
        old_formats = [item for item in (result.get("formats") or []) if item.get("format_id") != highest["format_id"]]
        result["formats"] = [highest] + old_formats
        result["highest_hls"] = {
            "width": selected.get("width") or 0,
            "height": selected.get("height") or 0,
            "bandwidth": selected.get("bandwidth") or 0,
        }
    return result


def run_download_v5(job_id: str, payload: dict) -> None:
    kind = str(payload.get("download_kind") or "video").strip().lower()
    media_url = str(payload.get("media_url") or "").strip()
    if kind == "video" and media_url.lower().split("?", 1)[0].endswith(".m3u8"):
        if run_ffmpeg_best_hls(job_id, payload):
            return
    v4.run_download_v4(job_id, payload)


core.probe_media = probe_media_v5
core.run_download = run_download_v5


if __name__ == "__main__":
    core.main()
