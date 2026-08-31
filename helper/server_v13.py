from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import server_v12 as v12

core = v12.core
core.VERSION = "0.6.2"

v11 = v12.v11
v10 = v12.v10
v9 = v12.v9
v8 = v12.v8
v5 = v12.v5

TS_PACKET = 188
SCAN_LIMIT = 2 * 1024 * 1024
MIN_TS_PACKETS = 4


def _ts_span(path: Path) -> tuple[int, int] | None:
    """Return the longest MPEG-TS payload span found near the start of a file.

    Some CDNs serve HLS objects with an image/garbage prefix even though the
    useful bytes are MPEG-TS. Passing those bytes unchanged through the old
    packaging path can create a full-size MP4 whose only visible video stream is
    a tiny PNG frame. We detect repeated 188-byte sync packets and strip both the
    prefix and any trailing wrapper bytes before FFmpeg sees the segment.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(SCAN_LIMIT)
    except Exception:
        return None
    if len(data) < TS_PACKET * MIN_TS_PACKETS:
        return None

    best: tuple[int, int] | None = None
    pos = data.find(b"\x47")
    while pos >= 0:
        count = 0
        cursor = pos
        while cursor < len(data) and data[cursor] == 0x47:
            count += 1
            cursor += TS_PACKET
        if count >= MIN_TS_PACKETS:
            end = pos + count * TS_PACKET
            if best is None or end - pos > best[1] - best[0]:
                best = (pos, end)
        pos = data.find(b"\x47", pos + 1)

    if best is None:
        return None

    start, scanned_end = best
    # Continue packet-by-packet past the scan window without loading the whole
    # segment into memory. Stop at the first non-sync packet and trim any wrapper
    # bytes after the TS payload.
    try:
        size = path.stat().st_size
        end = scanned_end
        with path.open("rb") as handle:
            while end < size:
                handle.seek(end)
                marker = handle.read(1)
                if marker != b"\x47":
                    break
                end += TS_PACKET
        if end - start >= TS_PACKET * MIN_TS_PACKETS:
            return start, min(end, size)
    except Exception:
        pass
    return best


def _mp4_start(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(min(SCAN_LIMIT, path.stat().st_size))
    except Exception:
        return None

    # ftyp normally starts at byte 4 (atom size precedes it). fMP4 media pieces
    # may instead start with styp or moof. Only accept a plausible atom size.
    for atom in (b"ftyp", b"styp", b"moof"):
        pos = data.find(atom)
        while pos >= 4:
            start = pos - 4
            atom_size = int.from_bytes(data[start:pos], "big", signed=False)
            if atom_size == 1 or 8 <= atom_size <= max(8, len(data) - start):
                return start
            pos = data.find(atom, pos + 1)
    return None


def _copy_range(source: Path, destination: Path, start: int, end: int | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        src.seek(max(0, start))
        remaining = None if end is None else max(0, end - start)
        while remaining is None or remaining > 0:
            chunk_size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            if remaining is not None:
                remaining -= len(chunk)


def _normalize_part(source: Path, destination_dir: Path, index: int) -> tuple[Path, str, bool]:
    ts = _ts_span(source)
    if ts:
        start, end = ts
        destination = destination_dir / f"segment-{index:06d}.ts"
        _copy_range(source, destination, start, end)
        return destination, "ts", start > 0 or end < source.stat().st_size

    mp4_start = _mp4_start(source)
    if mp4_start is not None:
        destination = destination_dir / f"segment-{index:06d}.mp4"
        _copy_range(source, destination, mp4_start)
        return destination, "mp4", mp4_start > 0

    # Unknown payload: preserve it so clean formats handled by FFmpeg are not
    # damaged. The strict final validator below prevents a bogus result from
    # being reported as success.
    destination = destination_dir / f"segment-{index:06d}.bin"
    shutil.copy2(source, destination)
    return destination, "unknown", False


def _normalize_init(source: Path | None, destination_dir: Path) -> Path | None:
    if not source or not source.exists():
        return None
    start = _mp4_start(source)
    destination = destination_dir / "init.mp4"
    if start is None:
        shutil.copy2(source, destination)
    else:
        _copy_range(source, destination, start)
    return destination


def _write_normalized_local_hls(temp_dir: Path, init_path: Path | None, parts: list[Path], segments: list[dict]) -> Path:
    normalized_dir = temp_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)

    normalized: list[Path] = []
    kinds: list[str] = []
    wrapper_repairs = 0
    for index, part in enumerate(parts):
        path, kind, repaired = _normalize_part(part, normalized_dir, index)
        normalized.append(path)
        kinds.append(kind)
        wrapper_repairs += int(repaired)

    # A TS playlist must not carry an fMP4 init map. If the payload is MP4/fMP4,
    # keep and normalize the init object.
    dominant_ts = bool(kinds) and sum(1 for kind in kinds if kind == "ts") >= math.ceil(len(kinds) * 0.75)
    normalized_init = None if dominant_ts else _normalize_init(init_path, normalized_dir)

    target = max([float(item.get("duration") or 0) for item in segments] or [1.0])
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{max(1, math.ceil(target))}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    if normalized_init:
        lines.append(f'#EXT-X-MAP:URI="normalized/{normalized_init.name}"')
    for path, item in zip(normalized, segments):
        duration = max(0.001, float(item.get("duration") or 0.001))
        lines.append(f"#EXTINF:{duration:.6f},")
        lines.append(f"normalized/{path.name}")
    lines.append("#EXT-X-ENDLIST")

    manifest = temp_dir / "local-normalized.m3u8"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Small sidecar is useful when diagnosing a future site without changing UI.
    (temp_dir / "normalization.txt").write_text(
        f"parts={len(parts)}\nwrapper_repairs={wrapper_repairs}\nkinds={','.join(kinds[:40])}\n",
        encoding="utf-8",
    )
    return manifest


def _strict_finalize(candidate: Path, expected_duration: float) -> tuple[bool, float]:
    valid, info, _reason = v12._playable_enough(candidate, expected_duration)
    duration = float(info.get("duration") or 0)
    if not valid:
        return False, duration

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    video_codec = str(info.get("video_codec") or "").lower()
    audio_codec = str(info.get("audio_codec") or "").lower()

    # This specifically blocks the failure the user observed: a large MP4 whose
    # visible track is a 1x1 PNG while the real TS bytes sit elsewhere in the
    # file. Only a conventional H.264 video track (plus AAC/MP3 or no audio) is
    # accepted as a final HLS product.
    if width < 64 or height < 64:
        return False, duration
    if video_codec not in {"h264", "avc1"}:
        return False, duration
    if info.get("has_audio") and audio_codec not in {"aac", "mp3"}:
        return False, duration
    return True, duration


def _always_compat_hls(manifest: Path, output: Path) -> tuple[bool, str]:
    """Reliability-first HLS finalizer.

    v0.6.1 still allowed a lossless copy path to win. Some malformed CDN HLS
    objects can look decodable to FFmpeg yet remain unusable in normal players.
    For HLS we now always rebuild from the normalized local playlist to standard
    H.264/AAC/yuv420p MP4, then validate the resulting track and duration.
    """
    expected = v12._manifest_duration(manifest)
    output.unlink(missing_ok=True)
    ok, error = v12._transcode_compat(manifest, output)
    if not ok:
        output.unlink(missing_ok=True)
        return False, f"HLS兼容重建失败：{error}"

    valid, actual = _strict_finalize(output, expected)
    if not valid:
        output.unlink(missing_ok=True)
        return False, f"HLS兼容重建后仍未通过播放校验：{actual:.2f}s / 预期 {expected:.2f}s"
    return True, ""


# Patch every HLS finishing path used by server_v10. This fixes the hole in
# v0.6.1 where strategy 1 was hardened but strategy 2/3 could still accept a bad
# MP4 after the first strategy failed.
v10._write_local_hls = _write_normalized_local_hls
v10._remux_local_hls = _always_compat_hls
v10._finalize_candidate = _strict_finalize


if __name__ == "__main__":
    core.main()
