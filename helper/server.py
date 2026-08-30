from __future__ import annotations

import ipaddress
import json
import shutil
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

HOST = "127.0.0.1"
PORT = 17891
VERSION = "0.2.0"
DOWNLOAD_DIR = Path.home() / "Downloads" / "OmniFetch"
TOOLS_DIR = Path(__file__).resolve().parent / "tools"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
ALLOWED_EXTENSION_ORIGINS = ("chrome-extension://", "edge-extension://")

PLATFORM_RULES = (
    (("x.com", "twitter.com"), "X / Twitter"),
    (("youtube.com", "youtu.be"), "YouTube"),
    (("tiktok.com",), "TikTok"),
    (("douyin.com", "iesdouyin.com"), "抖音"),
    (("bilibili.com", "b23.tv"), "哔哩哔哩"),
    (("instagram.com",), "Instagram"),
    (("facebook.com", "fb.watch"), "Facebook"),
    (("xiaohongshu.com", "xhslink.com"), "小红书"),
    (("kuaishou.com", "gifshow.com"), "快手"),
    (("vimeo.com",), "Vimeo"),
    (("twitch.tv",), "Twitch"),
    (("reddit.com", "redd.it"), "Reddit"),
    (("dailymotion.com", "dai.ly"), "Dailymotion"),
    (("soundcloud.com",), "SoundCloud"),
)


def now_ts() -> int:
    return int(time.time())


def json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def safe_http_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("只接受 http/https 地址")

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        raise ValueError("地址缺少有效主机名")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("不允许下载本机或局域网地址")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("不允许下载本机或局域网地址")
    except ValueError as exc:
        if str(exc) == "不允许下载本机或局域网地址":
            raise

    return value


def detect_platform(value: str) -> str:
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return "通用网页"
    for domains, name in PLATFORM_RULES:
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return name
    return "通用网页"


def find_ffmpeg() -> str | None:
    bundled = TOOLS_DIR / "ffmpeg.exe"
    if bundled.exists():
        return str(TOOLS_DIR)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return str(Path(system_ffmpeg).parent)
    return None


def update_job(job_id: str, **changes) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        job["updated_at"] = now_ts()


def progress_hook(job_id: str):
    def hook(data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = round((downloaded / total) * 100, 1) if total else None
            update_job(
                job_id,
                status="downloading",
                percent=percent,
                downloaded_bytes=downloaded,
                total_bytes=total or None,
                speed=data.get("speed"),
                eta=data.get("eta"),
                filename=data.get("filename") or "",
            )
        elif status == "finished":
            update_job(
                job_id,
                status="processing",
                percent=100,
                filename=data.get("filename") or "",
            )

    return hook


def base_ydl_options(job_id: str, page_url: str) -> dict:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg_location = find_ffmpeg()

    opts = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title).180B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "overwrites": False,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "progress_hooks": [progress_hook(job_id)],
        "http_headers": {
            "Referer": page_url or "",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        },
    }

    if ffmpeg_location:
        opts.update(
            {
                "format": "bv*+ba/b",
                "merge_output_format": "mp4",
                "ffmpeg_location": ffmpeg_location,
            }
        )
    else:
        opts["format"] = "b[ext=mp4]/b"

    return opts


def make_attempts(
    job_id: str,
    page_url: str,
    media_url: str,
    fallback_urls: list[str],
    browser: str,
) -> list[tuple[str, str, dict]]:
    base_opts = base_ydl_options(job_id, page_url)
    browser_ok = browser in {"chrome", "edge", "firefox"}
    attempts: list[tuple[str, str, dict]] = []

    def add_target(label: str, target: str, use_cookies: bool) -> None:
        if not target:
            return
        opts = dict(base_opts)
        opts["http_headers"] = dict(base_opts.get("http_headers") or {})
        if use_cookies and browser_ok:
            opts["cookiesfrombrowser"] = (browser,)
        attempts.append((label, target, opts))

    if media_url:
        if browser_ok:
            add_target("captured-media-cookies", media_url, True)
        add_target("captured-media", media_url, False)
        return attempts

    if page_url:
        if browser_ok:
            add_target("page-extractor-cookies", page_url, True)
        add_target("page-extractor", page_url, False)

    seen = {page_url, media_url, ""}
    for index, url in enumerate(fallback_urls[:10], start=1):
        if url in seen:
            continue
        seen.add(url)
        if browser_ok:
            add_target(f"captured-fallback-{index}-cookies", url, True)
        add_target(f"captured-fallback-{index}", url, False)

    return attempts


def run_download(job_id: str, payload: dict) -> None:
    try:
        page_url = safe_http_url(payload.get("page_url"))
        media_url = safe_http_url(payload.get("media_url"))
        browser = str(payload.get("browser") or "").strip().lower()
        title_hint = str(payload.get("title") or "").strip()

        raw_fallbacks = payload.get("fallback_media_urls") or []
        if not isinstance(raw_fallbacks, list):
            raw_fallbacks = []
        fallback_urls: list[str] = []
        for raw in raw_fallbacks[:12]:
            try:
                value = safe_http_url(str(raw))
            except ValueError:
                continue
            if value and value not in fallback_urls:
                fallback_urls.append(value)

        if not page_url and not media_url and not fallback_urls:
            raise ValueError("缺少可下载地址")

        platform = detect_platform(page_url or media_url or fallback_urls[0])
        update_job(
            job_id,
            status="starting",
            platform=platform,
            target_url=media_url or page_url or fallback_urls[0],
            fallback_count=len(fallback_urls),
        )

        attempts = make_attempts(job_id, page_url, media_url, fallback_urls, browser)
        if not attempts:
            raise ValueError("没有可用的下载策略")

        last_error: Exception | None = None
        total_attempts = len(attempts)
        for index, (mode, target_url, ydl_opts) in enumerate(attempts, start=1):
            update_job(
                job_id,
                status="resolving",
                mode=mode,
                strategy=mode,
                attempt=index,
                attempt_total=total_attempts,
                target_url=target_url,
                error=None,
            )
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    final_title = (info or {}).get("title") or title_hint or "video"
                    final_id = (info or {}).get("id") or ""
                    extractor = (info or {}).get("extractor_key") or (info or {}).get("extractor") or ""
                    update_job(
                        job_id,
                        status="completed",
                        percent=100,
                        title=final_title,
                        media_id=final_id,
                        extractor=extractor,
                        output_dir=str(DOWNLOAD_DIR),
                        strategy=mode,
                        error=None,
                    )
                    return
            except Exception as exc:
                last_error = exc
                if index < total_attempts:
                    update_job(
                        job_id,
                        status="retrying",
                        error=str(exc),
                        next_attempt=index + 1,
                        attempt_total=total_attempts,
                    )
                    continue
                raise

        if last_error:
            raise last_error
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            error=str(exc),
            traceback=traceback.format_exc(limit=6),
        )


def create_job(payload: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "percent": None,
            "created_at": now_ts(),
            "updated_at": now_ts(),
            "error": None,
        }
    thread = threading.Thread(target=run_download, args=(job_id, payload), daemon=True)
    thread.start()
    return job_id


class OmniFetchHandler(BaseHTTPRequestHandler):
    server_version = f"OmniFetchHelper/{VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip().lower()
        if not origin:
            return True
        return origin.startswith(ALLOWED_EXTENSION_ORIGINS)

    def _cors(self) -> None:
        origin = (self.headers.get("Origin") or "").strip()
        if origin.lower().startswith(ALLOWED_EXTENSION_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def send_json(self, status: int, data: dict) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self.send_json(403, {"ok": False, "error": "Origin not allowed"})
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._origin_allowed():
            self.send_json(403, {"ok": False, "error": "Origin not allowed"})
            return

        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "OmniFetch Helper",
                    "version": VERSION,
                    "download_dir": str(DOWNLOAD_DIR),
                    "ffmpeg": bool(find_ffmpeg()),
                    "strategy": "page extractor + captured media fallback",
                },
            )
            return

        if path == "/capabilities":
            self.send_json(
                200,
                {
                    "ok": True,
                    "version": VERSION,
                    "known_platforms": [name for _, name in PLATFORM_RULES],
                    "generic_capture": True,
                    "formats": ["MP4", "WebM", "M3U8/HLS", "DASH/MPD", "MOV", "M4V"],
                },
            )
            return

        if path.startswith("/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            if not job:
                self.send_json(404, {"ok": False, "error": "任务不存在"})
                return
            self.send_json(200, {"ok": True, "job": job})
            return

        if path == "/jobs":
            with JOBS_LOCK:
                jobs = sorted((dict(v) for v in JOBS.values()), key=lambda x: x["created_at"], reverse=True)[:50]
            self.send_json(200, {"ok": True, "jobs": jobs})
            return

        self.send_json(404, {"ok": False, "error": "Not Found"})

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self.send_json(403, {"ok": False, "error": "Origin not allowed"})
            return

        path = urlparse(self.path).path
        if path != "/download":
            self.send_json(404, {"ok": False, "error": "Not Found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ValueError("请求内容无效")
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式错误")

            page_url = safe_http_url(payload.get("page_url"))
            media_url = safe_http_url(payload.get("media_url"))
            raw_fallbacks = payload.get("fallback_media_urls") or []
            if not isinstance(raw_fallbacks, list):
                raise ValueError("fallback_media_urls 格式错误")

            fallback_urls: list[str] = []
            for raw_url in raw_fallbacks[:12]:
                try:
                    value = safe_http_url(str(raw_url))
                except ValueError:
                    continue
                if value and value not in fallback_urls:
                    fallback_urls.append(value)

            if not page_url and not media_url and not fallback_urls:
                raise ValueError("缺少 page_url、media_url 或 fallback_media_urls")

            payload["page_url"] = page_url
            payload["media_url"] = media_url
            payload["fallback_media_urls"] = fallback_urls
            job_id = create_job(payload)
            self.send_json(
                202,
                {
                    "ok": True,
                    "job_id": job_id,
                    "status": "queued",
                    "platform": detect_platform(page_url or media_url or fallback_urls[0]),
                    "output_dir": str(DOWNLOAD_DIR),
                },
            )
        except json.JSONDecodeError:
            self.send_json(400, {"ok": False, "error": "JSON 格式错误"})
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 62)
    print(f"OmniFetch Local Helper v{VERSION}")
    print(f"Listening : http://{HOST}:{PORT}")
    print(f"Downloads : {DOWNLOAD_DIR}")
    print(f"FFmpeg    : {'available' if find_ffmpeg() else 'not found (single-file fallback)'}")
    print("Mode      : multi-platform extractor + captured media fallback")
    print("Press Ctrl+C to stop.")
    print("=" * 62)

    server = ThreadingHTTPServer((HOST, PORT), OmniFetchHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("OmniFetch helper stopped.")


if __name__ == "__main__":
    main()
