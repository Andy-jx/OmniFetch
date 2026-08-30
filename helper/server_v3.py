from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path

import yt_dlp

import server_v2 as v2

core = v2.core
core.VERSION = "0.5.3"
MIN_VIDEO_BYTES = 32 * 1024


def safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{int(time.time())}{suffix}")


def final_media_file(temp_dir: Path) -> Path | None:
    if not temp_dir.exists():
        return None
    ignored_suffixes = {".part", ".ytdl", ".temp"}
    files = [
        path for path in temp_dir.rglob("*")
        if path.is_file() and path.suffix.lower() not in ignored_suffixes
    ]
    if not files:
        return None

    preferred = [
        path for path in files
        if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv"}
    ]
    pool = preferred or files
    return max(pool, key=lambda path: path.stat().st_size if path.exists() else 0)


def is_manifest_url(value: str) -> bool:
    lower = (value or "").lower()
    path = lower.split("?", 1)[0].split("#", 1)[0]
    return path.endswith(".m3u8") or path.endswith(".mpd")


def video_options(job_id: str, page_url: str, format_id: str = "") -> tuple[dict, Path]:
    temp_dir = core.DOWNLOAD_DIR / ".tmp" / job_id
    safe_rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    opts = core.base_ydl_options(job_id, page_url, format_id)
    opts["outtmpl"] = str(temp_dir / "%(title).180B [%(id)s].%(ext)s")
    opts["overwrites"] = True
    opts["continuedl"] = True
    return opts, temp_dir


def clone_opts(opts: dict) -> dict:
    cloned = dict(opts)
    cloned["http_headers"] = dict(opts.get("http_headers") or {})
    if isinstance(opts.get("progress_hooks"), list):
        cloned["progress_hooks"] = list(opts["progress_hooks"])
    return cloned


def run_video_download(job_id: str, payload: dict) -> None:
    temp_dir: Path | None = None
    try:
        page_url = core.safe_http_url(payload.get("page_url"))
        media_url = core.safe_http_url(payload.get("media_url"))
        browser = str(payload.get("browser") or "").strip().lower()
        title_hint = str(payload.get("title") or "").strip()
        format_id = str(payload.get("format_id") or "").strip()

        raw_fallbacks = payload.get("fallback_media_urls") or []
        if not isinstance(raw_fallbacks, list):
            raw_fallbacks = []
        fallback_urls: list[str] = []
        for raw in raw_fallbacks[:12]:
            try:
                value = core.safe_http_url(str(raw))
            except ValueError:
                continue
            if value and value not in fallback_urls:
                fallback_urls.append(value)

        if not page_url and not media_url and not fallback_urls:
            raise ValueError("缺少可下载地址")

        browser_ok = browser in {"chrome", "edge", "firefox"}
        base_opts, temp_dir = video_options(job_id, page_url, format_id)
        attempts: list[tuple[str, str, bool]] = []

        # FetchV-style behavior: a captured M3U8/MPD is the strongest source.
        # It describes the full stream, while .m4s/.ts/small mp4 responses may be fragments.
        if media_url and is_manifest_url(media_url):
            attempts.append(("captured-manifest", media_url, False))
            if browser_ok:
                attempts.append(("captured-manifest-cookies", media_url, True))

        if page_url:
            if browser_ok:
                attempts.append(("page-extractor-cookies", page_url, True))
            attempts.append(("page-extractor", page_url, False))

        if media_url and not is_manifest_url(media_url):
            attempts.append(("captured-media", media_url, False))
            if browser_ok:
                attempts.append(("captured-media-cookies", media_url, True))

        seen = {page_url, media_url, ""}
        manifest_fallbacks = [url for url in fallback_urls if is_manifest_url(url)]
        other_fallbacks = [url for url in fallback_urls if not is_manifest_url(url)]
        for index, url in enumerate(manifest_fallbacks + other_fallbacks, start=1):
            if url in seen:
                continue
            seen.add(url)
            label = "manifest-fallback" if is_manifest_url(url) else "captured-fallback"
            attempts.append((f"{label}-{index}", url, False))

        if not attempts:
            raise ValueError("没有可用的下载策略")

        platform = core.detect_platform(page_url or media_url or fallback_urls[0])
        core.update_job(
            job_id,
            status="starting",
            platform=platform,
            download_kind="video",
            target_url=attempts[0][1],
            attempt_total=len(attempts),
            minimum_video_bytes=MIN_VIDEO_BYTES,
            concurrent_fragments=8,
        )

        last_error: Exception | None = None
        for index, (mode, target, use_cookies) in enumerate(attempts, start=1):
            safe_rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            core.update_job(
                job_id,
                status="resolving",
                mode=mode,
                strategy=mode,
                attempt=index,
                attempt_total=len(attempts),
                target_url=target,
                error=None,
            )
            try:
                opts = clone_opts(base_opts)
                if use_cookies and browser_ok:
                    opts["cookiesfrombrowser"] = (browser,)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(target, download=True) or {}

                final_file = final_media_file(temp_dir)
                final_size = final_file.stat().st_size if final_file and final_file.exists() else 0
                if not final_file or final_size < MIN_VIDEO_BYTES:
                    raise RuntimeError(
                        f"下载结果异常，仅 {final_size} 字节；已自动判定为无效媒体并切换下一种下载策略"
                    )

                core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                destination = unique_destination(core.DOWNLOAD_DIR / final_file.name)
                shutil.move(str(final_file), str(destination))
                safe_rmtree(temp_dir)

                core.update_job(
                    job_id,
                    status="completed",
                    percent=100,
                    title=info.get("title") or title_hint or destination.stem,
                    media_id=info.get("id") or "",
                    extractor=info.get("extractor_key") or info.get("extractor") or "",
                    output_dir=str(core.DOWNLOAD_DIR),
                    output_file=str(destination),
                    output_bytes=destination.stat().st_size,
                    download_kind="video",
                    strategy=mode,
                    error=None,
                )
                return
            except Exception as exc:
                last_error = exc
                safe_rmtree(temp_dir)
                if index < len(attempts):
                    core.update_job(
                        job_id,
                        status="retrying",
                        error=str(exc),
                        next_attempt=index + 1,
                        attempt_total=len(attempts),
                    )
                    continue
                raise

        if last_error:
            raise last_error
    except Exception as exc:
        if temp_dir:
            safe_rmtree(temp_dir)
        core.update_job(
            job_id,
            status="failed",
            error=str(exc),
            traceback=traceback.format_exc(limit=8),
        )


def run_download(job_id: str, payload: dict) -> None:
    kind = str(payload.get("download_kind") or "video").strip().lower()
    if kind == "audio":
        v2.run_audio_download(job_id, payload)
        return
    run_video_download(job_id, payload)


core.run_download = run_download


if __name__ == "__main__":
    core.main()
