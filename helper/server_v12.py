from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import server_v11 as v11

core = v11.core
core.VERSION = "0.6.1"

v10 = v11.v10
v9 = v11.v9
v8 = v11.v8
v5 = v11.v5

_original_remux_local_hls = v10._remux_local_hls


def _ffmpeg() -> str | None:
    return v5.ffmpeg_executable()


def _manifest_duration(manifest: Path) -> float:
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0.0
    total = 0.0
    for value in re.findall(r"(?im)^#EXTINF:([0-9]+(?:\.[0-9]+)?)", text):
        try:
            total += max(0.0, float(value))
        except Exception:
            pass
    return total


def _media_info(path: Path) -> dict:
    ffmpeg = _ffmpeg()
    result = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "video_codec": "",
        "audio_codec": "",
        "has_video": False,
        "has_audio": False,
    }
    if not ffmpeg or not path.exists():
        return result

    cmd = [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return result

    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if duration_match:
        result["duration"] = (
            int(duration_match.group(1)) * 3600
            + int(duration_match.group(2)) * 60
            + float(duration_match.group(3))
        )

    for line in text.splitlines():
        if "Video:" in line and not result["has_video"]:
            result["has_video"] = True
            codec = re.search(r"Video:\s*([^,\s]+)", line)
            if codec:
                result["video_codec"] = codec.group(1).lower()
            dimensions = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", line)
            if dimensions:
                result["width"] = int(dimensions.group(1))
                result["height"] = int(dimensions.group(2))
        if "Audio:" in line and not result["has_audio"]:
            result["has_audio"] = True
            codec = re.search(r"Audio:\s*([^,\s]+)", line)
            if codec:
                result["audio_codec"] = codec.group(1).lower()

    return result


def _decode_window(path: Path, start: float, seconds: float = 1.5) -> tuple[bool, str]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg not found"

    cmd = [ffmpeg, "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += [
        "-i", str(path),
        "-t", f"{seconds:.3f}",
        "-map", "0:v:0",
        "-an",
        "-f", "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "decode failed").strip()[-1600:]


def _playable_enough(path: Path, expected_duration: float) -> tuple[bool, dict, str]:
    if not path.exists() or path.stat().st_size < v11.DIRECT_MIN_BYTES:
        return False, {}, "输出文件过小"

    info = _media_info(path)
    duration = float(info.get("duration") or 0)
    if not info.get("has_video") or int(info.get("width") or 0) <= 0 or int(info.get("height") or 0) <= 0:
        return False, info, "没有检测到有效视频轨"
    if duration <= 0.2:
        return False, info, "视频时长无效"
    if expected_duration > 2 and duration < expected_duration * 0.80:
        return False, info, f"视频时长异常：{duration:.2f}s / 预期约 {expected_duration:.2f}s"

    ok, error = _decode_window(path, 0.0)
    if not ok:
        return False, info, f"开头画面无法正常解码：{error}"

    if duration > 8:
        end_start = max(0.0, duration - 3.0)
        ok, error = _decode_window(path, end_start)
        if not ok:
            return False, info, f"结尾画面无法正常解码：{error}"

    return True, info, ""


def _copy_remux(manifest: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg not found"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+genpts+discardcorrupt",
        "-protocol_whitelist", "file,crypto,data",
        "-allowed_extensions", "ALL",
        "-i", str(manifest),
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-max_interleave_delta", "0",
        "-movflags", "+faststart",
        str(output),
    ]
    return v8._run_ffmpeg(cmd, timeout=1800)


def _transcode_compat(manifest: Path, output: Path) -> tuple[bool, str]:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg not found"

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",
        "-protocol_whitelist", "file,crypto,data",
        "-allowed_extensions", "ALL",
        "-i", str(manifest),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", "aresample=async=1:first_pts=0",
        "-avoid_negative_ts", "make_zero",
        "-max_muxing_queue_size", "4096",
        "-movflags", "+faststart",
        str(output),
    ]
    return v8._run_ffmpeg(cmd, timeout=14400)


def _robust_remux_local_hls(manifest: Path, output: Path) -> tuple[bool, str]:
    """Build a Windows-friendly MP4 from the already-downloaded local HLS.

    First try a lossless stream copy. The result is not accepted just because
    FFmpeg created a file: it must have a sane duration and decode both near the
    beginning and near the end. If that check fails, rebuild directly from the
    local HLS into H.264/AAC, which repairs broken timestamps/sample tables and
    avoids source codecs/containers that Windows players commonly reject.
    """
    expected = _manifest_duration(manifest)
    errors: list[str] = []

    output.unlink(missing_ok=True)
    ok, error = _copy_remux(manifest, output)
    if ok:
        valid, info, reason = _playable_enough(output, expected)
        if valid:
            vcodec = str(info.get("video_codec") or "")
            acodec = str(info.get("audio_codec") or "")
            # H.264 + AAC (or no audio) is the safest MP4 combination for the
            # Windows shell, Movies & TV, PotPlayer, VLC and most editors.
            if vcodec in {"h264", "avc1"} and (not info.get("has_audio") or acodec in {"aac", "mp3"}):
                return True, ""
            reason = f"容器可读但编码兼容性不足：video={vcodec or '?'} audio={acodec or 'none'}"
        errors.append(f"copy-remux: {reason}")
    else:
        errors.append(f"copy-remux: {error}")

    output.unlink(missing_ok=True)
    ok, error = _transcode_compat(manifest, output)
    if not ok:
        errors.append(f"compat-transcode: {error}")
        return False, " | ".join(errors[-3:])

    valid, info, reason = _playable_enough(output, expected)
    if not valid:
        errors.append(f"compat-transcode: {reason}")
        output.unlink(missing_ok=True)
        return False, " | ".join(errors[-3:])

    return True, ""


# server_v10 resolves this global at runtime. Replacing it here keeps all of the
# v0.6.0 multi-candidate/HLS selection logic but hardens only the final packaging
# stage, so a full-size yet unplayable MP4 can no longer be reported as success.
v10._remux_local_hls = _robust_remux_local_hls


if __name__ == "__main__":
    core.main()
