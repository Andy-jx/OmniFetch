from __future__ import annotations

import traceback

import yt_dlp

import server as core

core.VERSION = "0.5.2"
ORIGINAL_RUN_DOWNLOAD = core.run_download


def audio_options(job_id: str, page_url: str, browser: str, use_cookies: bool) -> dict:
    core.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    opts = core.common_ydl_options(page_url)
    opts.update(
        {
            "outtmpl": str(core.DOWNLOAD_DIR / "%(title).180B [audio-%(id)s].%(ext)s"),
            "overwrites": False,
            "continuedl": True,
            "format": "bestaudio/best",
            "progress_hooks": [core.progress_hook(job_id)],
        }
    )

    ffmpeg_location = core.find_ffmpeg()
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            }
        ]

    if use_cookies and browser in {"chrome", "edge", "firefox"}:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def run_audio_download(job_id: str, payload: dict) -> None:
    try:
        page_url = core.safe_http_url(payload.get("page_url"))
        media_url = core.safe_http_url(payload.get("media_url"))
        browser = str(payload.get("browser") or "").strip().lower()
        title_hint = str(payload.get("title") or "").strip()

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

        targets: list[tuple[str, str, bool]] = []
        if page_url:
            if browser in {"chrome", "edge", "firefox"}:
                targets.append(("page-audio-cookies", page_url, True))
            targets.append(("page-audio", page_url, False))
        if media_url:
            targets.append(("captured-audio", media_url, False))
            if browser in {"chrome", "edge", "firefox"}:
                targets.append(("captured-audio-cookies", media_url, True))
        for index, url in enumerate(fallback_urls[:8], start=1):
            targets.append((f"fallback-audio-{index}", url, False))

        if not targets:
            raise ValueError("缺少可提取音频的页面或媒体地址")

        platform = core.detect_platform(page_url or media_url or fallback_urls[0])
        core.update_job(
            job_id,
            status="starting",
            platform=platform,
            download_kind="audio",
            target_url=targets[0][1],
            concurrent_fragments=8,
        )

        last_error: Exception | None = None
        for index, (mode, target, use_cookies) in enumerate(targets, start=1):
            core.update_job(
                job_id,
                status="resolving",
                mode=mode,
                strategy=mode,
                attempt=index,
                attempt_total=len(targets),
                target_url=target,
                error=None,
            )
            try:
                opts = audio_options(job_id, page_url, browser, use_cookies)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(target, download=True) or {}
                core.update_job(
                    job_id,
                    status="completed",
                    percent=100,
                    title=info.get("title") or title_hint or "audio",
                    media_id=info.get("id") or "",
                    extractor=info.get("extractor_key") or info.get("extractor") or "",
                    output_dir=str(core.DOWNLOAD_DIR),
                    download_kind="audio",
                    strategy=mode,
                    error=None,
                )
                return
            except Exception as exc:
                last_error = exc
                if index < len(targets):
                    core.update_job(
                        job_id,
                        status="retrying",
                        error=str(exc),
                        next_attempt=index + 1,
                        attempt_total=len(targets),
                    )
                    continue
                raise

        if last_error:
            raise last_error
    except Exception as exc:
        core.update_job(
            job_id,
            status="failed",
            error=str(exc),
            traceback=traceback.format_exc(limit=6),
        )


def run_download(job_id: str, payload: dict) -> None:
    if str(payload.get("download_kind") or "").strip().lower() == "audio":
        run_audio_download(job_id, payload)
        return
    ORIGINAL_RUN_DOWNLOAD(job_id, payload)


core.run_download = run_download


if __name__ == "__main__":
    core.main()
