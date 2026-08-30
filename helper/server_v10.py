from __future__ import annotations

import concurrent.futures
import math
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

import server_v9 as v9

core = v9.core
core.VERSION = "0.5.10"


def _quality_p(width: int, height: int) -> int:
    if width > 0 and height > 0:
        return min(width, height)
    return height or width or 0


def _parse_media_playlist_v10(playlist_url: str, text: str) -> tuple[dict | None, list[dict], float]:
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    init_segment: dict | None = None
    segments: list[dict] = []
    pending_range: tuple[int, int] | None = None
    previous_end: int | None = None
    pending_duration = 0.0
    expected_duration = 0.0

    for line in lines:
        upper = line.upper()
        if upper.startswith("#EXT-X-KEY:"):
            attrs = v9.v8.v7.v5.parse_attr_list(line.split(":", 1)[1])
            method = str(attrs.get("METHOD") or "NONE").upper()
            if method not in {"", "NONE"}:
                raise RuntimeError("检测到加密 HLS，本工具不绕过 DRM/加密访问控制")
            continue
        if upper.startswith("#EXT-X-MAP:"):
            attrs = v9.v8.v7.v5.parse_attr_list(line.split(":", 1)[1])
            uri = str(attrs.get("URI") or "").strip()
            if uri:
                br = v9._parse_byterange(attrs.get("BYTERANGE") or "")
                init_segment = {"url": core.safe_http_url(urljoin(playlist_url, uri)), "range": br}
            continue
        if upper.startswith("#EXT-X-BYTERANGE:"):
            pending_range = v9._parse_byterange(line.split(":", 1)[1], previous_end)
            continue
        if upper.startswith("#EXTINF:"):
            raw = line.split(":", 1)[1].split(",", 1)[0].strip()
            try:
                pending_duration = max(0.0, float(raw))
            except Exception:
                pending_duration = 0.0
            continue
        if not line or line.startswith("#"):
            continue

        url = core.safe_http_url(urljoin(playlist_url, line))
        segments.append({"url": url, "range": pending_range, "duration": pending_duration})
        expected_duration += pending_duration
        if pending_range:
            previous_end = pending_range[0] + pending_range[1]
        else:
            previous_end = None
        pending_range = None
        pending_duration = 0.0

    if not segments:
        raise RuntimeError("播放清单中没有媒体分片")
    return init_segment, segments, expected_duration


def _probe_duration(path: Path) -> float:
    ffmpeg = v9.v8.v7.v5.ffmpeg_executable()
    if not ffmpeg or not path.exists():
        return 0.0
    cmd = [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"]
    try:
        proc = v9.subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(v9.subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return 0.0
    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _duration_ok(output: Path, expected: float) -> tuple[bool, float]:
    actual = _probe_duration(output)
    if expected <= 1.0:
        return output.exists() and output.stat().st_size >= v9.v8.v7.v5.v4.v3.MIN_VIDEO_BYTES, actual
    # HLS EXTINF values are approximate, so allow a generous 20% margin.
    return actual >= max(1.0, expected * 0.80), actual


def _write_local_hls(temp_dir: Path, init_path: Path | None, parts: list[Path], segments: list[dict]) -> Path:
    target = max([float(item.get("duration") or 0) for item in segments] or [1.0])
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{max(1, math.ceil(target))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    if init_path:
        lines.append(f'#EXT-X-MAP:URI="{init_path.name}"')
    for path, item in zip(parts, segments):
        duration = float(item.get("duration") or 0)
        lines.append(f"#EXTINF:{duration:.6f},")
        lines.append(f"parts/{path.name}")
    lines.append("#EXT-X-ENDLIST")
    manifest = temp_dir / "local.m3u8"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def _remux_local_hls(manifest: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = v9.v8.v7.v5.ffmpeg_executable()
    if not ffmpeg:
        return False, "FFmpeg not found"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,crypto,data",
        "-allowed_extensions",
        "ALL",
        "-i",
        str(manifest),
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
    return v9.v8._run_ffmpeg(cmd, timeout=1800)


def _write_concat_with_durations(parts: list[Path], segments: list[dict], folder: Path) -> Path:
    concat_file = folder / "concat-duration.txt"
    lines: list[str] = []
    for path, item in zip(parts, segments):
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        duration = float(item.get("duration") or 0)
        if duration > 0:
            lines.append(f"duration {duration:.6f}")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return concat_file


def _concat_demux_with_durations(parts: list[Path], segments: list[dict], folder: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = v9.v8.v7.v5.ffmpeg_executable()
    if not ffmpeg:
        return False, "FFmpeg not found"
    concat_file = _write_concat_with_durations(parts, segments, folder)
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
        "-fflags",
        "+genpts",
        "-avoid_negative_ts",
        "make_zero",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return v9.v8._run_ffmpeg(cmd, timeout=1800)


def _make_self_contained_parts(init_path: Path, parts: list[Path], folder: Path) -> list[Path]:
    complete_dir = folder / "complete-parts"
    complete_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, part in enumerate(parts):
        destination = complete_dir / f"segment-{index:06d}.mp4"
        v9._concat_binary([init_path, part], destination)
        outputs.append(destination)
    return outputs


def _finalize_candidate(candidate: Path, expected_duration: float) -> tuple[bool, float]:
    if not candidate.exists() or candidate.stat().st_size < v9.v8.v7.v5.v4.v3.MIN_VIDEO_BYTES:
        return False, 0.0
    return _duration_ok(candidate, expected_duration)


def run_native_hls_video_v10(job_id: str, payload: dict) -> bool:
    media_url = str(payload.get("media_url") or "").strip()
    if not v9.v8.is_hls_url(media_url):
        return False

    headers = v9.v8._headers_for(payload)
    try:
        selected = v9.v8._selected_hls(payload, headers)
    except Exception as exc:
        core.update_job(job_id, status="retrying", strategy="native-hls-v10", error=f"最高画质清单解析失败：{exc}")
        return False

    playlist_url = str(selected.get("video_url") or media_url).strip()
    width = int(selected.get("width") or payload.get("playback_width") or 0)
    height = int(selected.get("height") or payload.get("playback_height") or 0)
    quality_p = int(selected.get("quality_p") or _quality_p(width, height))

    try:
        text, _ = v9._fetch_playlist(playlist_url, payload)
        if "#EXT-X-STREAM-INF:" in text.upper():
            nested = v9.v8.v7.select_best_hls_v7(playlist_url, headers)
            playlist_url = str(nested.get("video_url") or playlist_url)
            width = int(nested.get("width") or width)
            height = int(nested.get("height") or height)
            quality_p = int(nested.get("quality_p") or _quality_p(width, height))
            text, _ = v9._fetch_playlist(playlist_url, payload)
        init_segment, segments, expected_duration = _parse_media_playlist_v10(playlist_url, text)
    except Exception as exc:
        core.update_job(job_id, status="retrying", strategy="native-hls-v10", error=f"M3U8 分片解析失败：{exc}")
        return False

    temp_dir = core.DOWNLOAD_DIR / ".tmp" / f"{job_id}-native-hls-v10"
    v9.v8.v7.v5.v4.v3.safe_rmtree(temp_dir)
    parts_dir = temp_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    try:
        core.update_job(
            job_id,
            status="downloading",
            percent=0,
            strategy="native-hls-v10-8x",
            target_url=playlist_url,
            selected_width=width,
            selected_height=height,
            selected_quality_p=quality_p,
            selected_quality=f"{width}x{height}" if width and height else "best available",
            fragment_index=0,
            fragment_count=len(segments),
            downloaded_bytes=0,
            expected_duration=round(expected_duration, 3),
            error=None,
        )

        init_path: Path | None = None
        if init_segment:
            _, init_path, _ = v9._download_one(999999, init_segment, parts_dir, payload)
            renamed = temp_dir / "init.bin"
            init_path.replace(renamed)
            init_path = renamed

        completed = 0
        downloaded = 0
        part_paths: list[Path | None] = [None] * len(segments)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(segments)))) as pool:
            futures = {
                pool.submit(v9._download_one, index, item, parts_dir, payload): index
                for index, item in enumerate(segments)
            }
            for future in concurrent.futures.as_completed(futures):
                index, path, size = future.result()
                part_paths[index] = path
                completed += 1
                downloaded += size
                core.update_job(
                    job_id,
                    status="downloading",
                    percent=round((completed / len(segments)) * 90, 1),
                    fragment_index=completed,
                    fragment_count=len(segments),
                    downloaded_bytes=downloaded,
                )

        ordered = [path for path in part_paths if path is not None]
        if len(ordered) != len(segments):
            raise RuntimeError("部分分片未成功保存")

        core.update_job(job_id, status="processing", percent=92, downloaded_bytes=downloaded)
        final_output = temp_dir / "output.mp4"
        errors: list[str] = []

        # Strategy 1: rebuild a local HLS manifest from the downloaded media.
        # This preserves EXTINF timing and is the closest equivalent to a
        # dedicated M3U8 downloader after all network requests are complete.
        manifest = _write_local_hls(temp_dir, init_path, ordered, segments)
        ok, error = _remux_local_hls(manifest, final_output)
        valid, actual_duration = _finalize_candidate(final_output, expected_duration) if ok else (False, 0.0)
        if not valid:
            errors.append(f"local-hls: {error or f'duration {actual_duration:.2f}s / expected {expected_duration:.2f}s'}")
            final_output.unlink(missing_ok=True)

        # Strategy 2: for MP4/fMP4 pieces, explicitly give FFmpeg each EXTINF
        # duration so it offsets every segment instead of keeping overlapping
        # timestamps that can make a full-size file report only a few seconds.
        if not valid:
            concat_parts = ordered
            if init_path:
                concat_parts = _make_self_contained_parts(init_path, ordered, temp_dir)
            ok, error = _concat_demux_with_durations(concat_parts, segments, temp_dir, final_output)
            valid, actual_duration = _finalize_candidate(final_output, expected_duration) if ok else (False, 0.0)
            if not valid:
                errors.append(f"duration-concat: {error or f'duration {actual_duration:.2f}s / expected {expected_duration:.2f}s'}")
                final_output.unlink(missing_ok=True)

        # Strategy 3: TS streams remain safest as binary-concatenated transport
        # streams. Regenerate timestamps during the final remux.
        if not valid and not init_path and v9._container_kind(ordered[0]) == "ts":
            combined_ts = temp_dir / "combined.ts"
            v9._concat_binary(ordered, combined_ts)
            ffmpeg = v9.v8.v7.v5.ffmpeg_executable()
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+genpts",
                "-i",
                str(combined_ts),
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                str(final_output),
            ]
            ok, error = v9.v8._run_ffmpeg(cmd, timeout=1800)
            valid, actual_duration = _finalize_candidate(final_output, expected_duration) if ok else (False, 0.0)
            if not valid:
                errors.append(f"ts-remux: {error or f'duration {actual_duration:.2f}s / expected {expected_duration:.2f}s'}")
                final_output.unlink(missing_ok=True)

        if not valid:
            raise RuntimeError("封装后时长校验失败；" + " | ".join(errors[-3:]))

        core.update_job(
            job_id,
            status="processing",
            percent=98,
            output_duration=round(actual_duration, 3),
            expected_duration=round(expected_duration, 3),
        )

        core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        title = v9.v8.v7.v5.safe_title(payload.get("title") or "video")
        suffix = f" [{quality_p}p]" if quality_p else ""
        destination = v9.v8.v7.v5.v4.v3.unique_destination(core.DOWNLOAD_DIR / f"{title}{suffix}.mp4")
        shutil.move(str(final_output), str(destination))
        v9.v8.v7.v5.v4.v3.safe_rmtree(temp_dir)

        core.update_job(
            job_id,
            status="completed",
            percent=100,
            title=title,
            output_dir=str(core.DOWNLOAD_DIR),
            output_file=str(destination),
            output_bytes=destination.stat().st_size,
            output_duration=round(actual_duration, 3),
            expected_duration=round(expected_duration, 3),
            download_kind="video",
            strategy="native-hls-v10-timeline-repair",
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
        v9.v8.v7.v5.v4.v3.safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="failed",
            strategy="native-hls-v10-timeline-repair",
            error=f"M3U8 分片已下载，但最终封装失败：{exc}",
        )
        return False


def run_download_v10(job_id: str, payload: dict) -> None:
    media_url = str(payload.get("media_url") or "").strip()
    kind = str(payload.get("download_kind") or "video").strip().lower()

    if v9.v8.is_hls_url(media_url) and kind == "video":
        # Do not silently accept a full-size MP4 whose timeline is only a few
        # seconds. v10 validates final duration against the source EXTINF sum.
        run_native_hls_video_v10(job_id, payload)
        return

    v9.run_download_v9(job_id, payload)


core.run_download = run_download_v10


if __name__ == "__main__":
    core.main()
