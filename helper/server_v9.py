from __future__ import annotations

import concurrent.futures
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import server_v8 as v8

core = v8.core
core.VERSION = "0.5.9"

MIN_PART_BYTES = 16
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024


def _quality_p(width: int, height: int) -> int:
    if width > 0 and height > 0:
        return min(width, height)
    return height or width or 0


def _header_profiles(payload: dict, target_url: str) -> list[dict[str, str]]:
    base = v8._headers_for(payload)
    page_url = str(payload.get("page_url") or "").strip()
    media_url = str(payload.get("media_url") or "").strip()
    default_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
    ua = base.get("User-Agent") or default_ua

    profiles: list[dict[str, str]] = []

    def add(headers: dict[str, str]) -> None:
        cleaned = {str(k): str(v) for k, v in headers.items() if k and v}
        key = tuple(sorted(cleaned.items()))
        if key not in {tuple(sorted(item.items())) for item in profiles}:
            profiles.append(cleaned)

    add(base)

    if media_url:
        p = dict(base)
        p["Referer"] = media_url
        add(p)

    p = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": base.get("Accept-Language") or "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if page_url:
        p["Referer"] = page_url
    add(p)

    p2 = {
        "User-Agent": ua,
        "Accept": "*/*",
    }
    add(p2)

    # Some CDN object URLs reject the original site's Origin/Cookie/Referer.
    # Keep a minimal browser-like profile as the final public-CDN attempt.
    add({"User-Agent": ua})
    return profiles


def _looks_like_html(head: bytes, content_type: str) -> bool:
    ctype = str(content_type or "").lower()
    sample = bytes(head or b"")[:1024].lstrip().lower()
    if "text/html" in ctype or "application/json" in ctype:
        return True
    return (
        sample.startswith(b"<!doctype html")
        or sample.startswith(b"<html")
        or b"<html" in sample[:300]
        or sample.startswith(b"{\"error\"")
    )


def _fetch_small(url: str, payload: dict, limit: int = MAX_PLAYLIST_BYTES) -> tuple[bytes, dict[str, str]]:
    errors: list[str] = []
    for headers in _header_profiles(payload, url):
        try:
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=20) as response:
                data = response.read(limit + 1)
                ctype = str(response.headers.get("Content-Type") or "")
            if len(data) > limit:
                raise RuntimeError("response is unexpectedly large")
            if _looks_like_html(data, ctype):
                raise RuntimeError(f"server returned {ctype or 'HTML/text'} instead of media")
            return data, headers
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" / ".join(errors[-3:]) or "request failed")


def _fetch_playlist(url: str, payload: dict) -> tuple[str, dict[str, str]]:
    data, headers = _fetch_small(url, payload)
    text = data.decode("utf-8", errors="replace")
    if "#EXTM3U" not in text[:1024]:
        raise RuntimeError("返回内容不是有效 M3U8")
    return text, headers


def _parse_byterange(value: str, previous_end: int | None = None) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)(?:@(\d+))?\s*", str(value or ""))
    if not match:
        return None
    length = int(match.group(1))
    offset = int(match.group(2)) if match.group(2) is not None else int(previous_end or 0)
    if length <= 0 or offset < 0:
        return None
    return offset, length


def _parse_media_playlist(playlist_url: str, text: str) -> tuple[dict | None, list[dict]]:
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    init_segment: dict | None = None
    segments: list[dict] = []
    pending_range: tuple[int, int] | None = None
    previous_end: int | None = None

    for line in lines:
        upper = line.upper()
        if upper.startswith("#EXT-X-KEY:"):
            attrs = v8.v7.v5.parse_attr_list(line.split(":", 1)[1])
            method = str(attrs.get("METHOD") or "NONE").upper()
            if method not in {"", "NONE"}:
                raise RuntimeError("检测到加密 HLS，本工具不绕过 DRM/加密访问控制")
            continue
        if upper.startswith("#EXT-X-MAP:"):
            attrs = v8.v7.v5.parse_attr_list(line.split(":", 1)[1])
            uri = str(attrs.get("URI") or "").strip()
            if uri:
                br = _parse_byterange(attrs.get("BYTERANGE") or "")
                init_segment = {"url": core.safe_http_url(urljoin(playlist_url, uri)), "range": br}
            continue
        if upper.startswith("#EXT-X-BYTERANGE:"):
            pending_range = _parse_byterange(line.split(":", 1)[1], previous_end)
            continue
        if not line or line.startswith("#"):
            continue

        url = core.safe_http_url(urljoin(playlist_url, line))
        segments.append({"url": url, "range": pending_range})
        if pending_range:
            previous_end = pending_range[0] + pending_range[1]
        else:
            previous_end = None
        pending_range = None

    if not segments:
        raise RuntimeError("播放清单中没有媒体分片")
    return init_segment, segments


def _download_one(index: int, item: dict, folder: Path, payload: dict) -> tuple[int, Path, int]:
    url = str(item.get("url") or "")
    byte_range = item.get("range")
    errors: list[str] = []

    for profile in _header_profiles(payload, url):
        headers = dict(profile)
        if byte_range:
            offset, length = byte_range
            headers["Range"] = f"bytes={offset}-{offset + length - 1}"
        try:
            req = Request(url, headers=headers, method="GET")
            path = folder / f"part-{index:06d}.bin"
            with urlopen(req, timeout=45) as response, path.open("wb") as output:
                ctype = str(response.headers.get("Content-Type") or "")
                head = response.read(1024)
                if _looks_like_html(head, ctype):
                    raise RuntimeError(f"CDN 返回 {ctype or 'HTML/text'}，不是媒体数据")
                output.write(head)
                total = len(head)
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    output.write(chunk)
                    total += len(chunk)
            if total < MIN_PART_BYTES:
                raise RuntimeError(f"分片仅 {total} 字节")
            return index, path, total
        except Exception as exc:
            errors.append(str(exc))
            try:
                (folder / f"part-{index:06d}.bin").unlink(missing_ok=True)
            except Exception:
                pass

    host = urlparse(url).hostname or url
    raise RuntimeError(f"分片 {index + 1} 下载失败 ({host})：" + " / ".join(errors[-3:]))


def _container_kind(path: Path) -> str:
    try:
        data = path.read_bytes()[:4096]
    except Exception:
        return "unknown"
    if len(data) >= 8 and (data[4:8] in {b"ftyp", b"styp", b"moov", b"moof"} or b"ftyp" in data[:64]):
        return "mp4"
    if len(data) >= 376 and data[0] == 0x47 and data[188] == 0x47:
        return "ts"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return "unknown"


def _concat_binary(paths: list[Path], destination: Path) -> None:
    with destination.open("wb") as output:
        for path in paths:
            with path.open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _ffmpeg_remux(source: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = v8.v7.v5.ffmpeg_executable()
    if not ffmpeg:
        return False, "FFmpeg not found"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return v8._run_ffmpeg(cmd, timeout=1800)


def _ffmpeg_concat(parts: list[Path], folder: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = v8.v7.v5.ffmpeg_executable()
    if not ffmpeg:
        return False, "FFmpeg not found"
    concat_file = folder / "concat.txt"
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(lines), encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return v8._run_ffmpeg(cmd, timeout=1800)


def run_native_hls_video(job_id: str, payload: dict) -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    if not v8.is_hls_url(media_url):
        return False

    headers = v8._headers_for(payload)
    try:
        selected = v8._selected_hls(payload, headers)
    except Exception as exc:
        core.update_job(job_id, status="retrying", strategy="native-hls", error=f"最高画质清单解析失败：{exc}")
        return False

    playlist_url = str(selected.get("video_url") or media_url).strip()
    width = int(selected.get("width") or payload.get("playback_width") or 0)
    height = int(selected.get("height") or payload.get("playback_height") or 0)
    quality_p = int(selected.get("quality_p") or _quality_p(width, height))

    try:
        text, _ = _fetch_playlist(playlist_url, payload)
        # A second master layer is uncommon but valid. Reuse the highest-variant
        # parser once more before treating it as a media playlist.
        if "#EXT-X-STREAM-INF:" in text.upper():
            nested = v8.v7.select_best_hls_v7(playlist_url, headers)
            playlist_url = str(nested.get("video_url") or playlist_url)
            width = int(nested.get("width") or width)
            height = int(nested.get("height") or height)
            quality_p = int(nested.get("quality_p") or _quality_p(width, height))
            text, _ = _fetch_playlist(playlist_url, payload)
        init_segment, segments = _parse_media_playlist(playlist_url, text)
    except Exception as exc:
        core.update_job(job_id, status="retrying", strategy="native-hls", error=f"M3U8 分片解析失败：{exc}")
        return False

    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-native-hls"
    v8.v7.v5.v4.v3.safe_rmtree(temp_dir)
    parts_dir = temp_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    try:
        core.update_job(
            job_id,
            status="downloading",
            percent=0,
            strategy="native-hls-8x",
            target_url=playlist_url,
            selected_width=width,
            selected_height=height,
            selected_quality_p=quality_p,
            selected_quality=f"{width}x{height}" if width and height else "best available",
            fragment_index=0,
            fragment_count=len(segments),
            downloaded_bytes=0,
            error=None,
        )

        init_path: Path | None = None
        if init_segment:
            _, init_path, _ = _download_one(999999, init_segment, parts_dir, payload)
            renamed = parts_dir / "init.bin"
            init_path.replace(renamed)
            init_path = renamed

        completed = 0
        downloaded = 0
        part_paths: list[Path | None] = [None] * len(segments)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(segments)))) as pool:
            futures = {
                pool.submit(_download_one, index, item, parts_dir, payload): index
                for index, item in enumerate(segments)
            }
            for future in concurrent.futures.as_completed(futures):
                index, path, size = future.result()
                part_paths[index] = path
                completed += 1
                downloaded += size
                percent = round((completed / len(segments)) * 92, 1)
                core.update_job(
                    job_id,
                    status="downloading",
                    percent=percent,
                    fragment_index=completed,
                    fragment_count=len(segments),
                    downloaded_bytes=downloaded,
                )

        ordered = [path for path in part_paths if path is not None]
        if len(ordered) != len(segments):
            raise RuntimeError("部分分片未成功保存")

        core.update_job(job_id, status="processing", percent=94, downloaded_bytes=downloaded)
        output = temp_dir / "output.mp4"
        kind = _container_kind(ordered[0])
        ok = False
        error = ""

        if init_path:
            combined = temp_dir / "combined.mp4"
            _concat_binary([init_path] + ordered, combined)
            ok, error = _ffmpeg_remux(combined, output)
        elif len(ordered) == 1:
            ok, error = _ffmpeg_remux(ordered[0], output)
        elif kind == "ts":
            combined = temp_dir / "combined.ts"
            _concat_binary(ordered, combined)
            ok, error = _ffmpeg_remux(combined, output)
        elif kind == "mp4":
            ok, error = _ffmpeg_concat(ordered, temp_dir, output)
            if not ok:
                combined = temp_dir / "combined.mp4"
                _concat_binary(ordered, combined)
                ok, error = _ffmpeg_remux(combined, output)
        else:
            ok, error = _ffmpeg_concat(ordered, temp_dir, output)
            if not ok:
                combined = temp_dir / "combined.bin"
                _concat_binary(ordered, combined)
                ok, error = _ffmpeg_remux(combined, output)

        size = output.stat().st_size if output.exists() else 0
        if not ok or size < v8.v7.v5.v4.v3.MIN_VIDEO_BYTES:
            raise RuntimeError(error or f"合并结果仅 {size} 字节")

        core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        title = v8.v7.v5.safe_title(payload.get("title") or "video")
        suffix = f" [{quality_p}p]" if quality_p else ""
        destination = v8.v7.v5.v4.v3.unique_destination(core.DOWNLOAD_DIR / f"{title}{suffix}.mp4")
        shutil.move(str(output), str(destination))
        v8.v7.v5.v4.v3.safe_rmtree(temp_dir)

        core.update_job(
            job_id,
            status="completed",
            percent=100,
            title=title,
            output_dir=str(core.DOWNLOAD_DIR),
            output_file=str(destination),
            output_bytes=destination.stat().st_size,
            download_kind="video",
            strategy="native-hls-8x",
            selected_width=width,
            selected_height=height,
            selected_quality_p=quality_p,
            selected_quality=f"{width}x{height}" if width and height else "best available",
            fragment_index=len(segments),
            fragment_count=len(segments),
            downloaded_bytes=downloaded,
            error=None,
        )
        return True
    except Exception as exc:
        v8.v7.v5.v4.v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="retrying",
            strategy="native-hls-8x",
            error=f"原生 M3U8 分片下载失败：{exc}",
        )
        return False


def run_download_v9(job_id: str, payload: dict) -> None:
    media_url = str(payload.get("media_url") or "").strip()
    kind = str(payload.get("download_kind") or "video").strip().lower()

    if v8.is_hls_url(media_url) and kind == "video":
        # FetchV-style path: download the actual HLS parts ourselves first.
        # This handles playlists whose first media object is a CDN-hosted MP4/
        # fMP4 object that FFmpeg's HLS demuxer refuses to open directly.
        if run_native_hls_video(job_id, payload):
            return
        # Still stay on the captured M3U8 path. v8 uses FFmpeg only and never
        # falls back to yt-dlp generic webpage extraction.
        v8.run_direct_hls_video(job_id, payload)
        return

    v8.run_download_v8(job_id, payload)


core.run_download = run_download_v9


if __name__ == "__main__":
    core.main()
