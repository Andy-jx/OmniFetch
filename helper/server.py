from __future__ import annotations

import ipaddress
import json
import re
import shutil
import sys
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
VERSION = "0.4.0"
DOWNLOAD_DIR = Path.home() / "Downloads" / "OmniFetch"
TOOLS_DIR = Path(__file__).resolve().parent / "tools"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
ALLOWED_EXTENSION_ORIGINS = ("chrome-extension://", "edge-extension://")
FORMAT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")

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
    candidates = [
        TOOLS_DIR / "ffmpeg.exe",
        Path(sys.executable).resolve().parent / "ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.parent)

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
            fragment_index = data.get("fragment_index") or 0
            fragment_count = data.get("fragment_count") or 0
            if total:
                percent = round((downloaded / total) * 100, 1)
            elif fragment_count:
                percent = round((fragment_index / fragment_count) * 100, 1)
            else:
                percent = None
            update_job(
                job_id,
                status="downloading",
                percent=percent,
                downloaded_bytes=downloaded,
                total_bytes=total or None,
                speed=data.get("speed"),
                eta=data.get("eta"),
                fragment_index=fragment_index or None,
                fragment_count=fragment_count or None,
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


def common_ydl_options(page_url: str) -> dict:
    return {
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 8,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 8,
        "http_headers": {
            "Referer": page_url or "",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        },
    }


def base_ydl_options(job_id: str, page_url: str, format_id: str = "") -> dict:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg_location = find_ffmpeg()
    opts = common_ydl_options(page_url)
    opts.update(
        {
            "outtmpl": str(DOWNLOAD_DIR / "%(title).180B [%(id)s].%(ext)s"),
            "overwrites": False,
            "continuedl": True,
            "progress_hooks": [progress_hook(job_id)],
        }
    )

    if format_id:
        if not FORMAT_ID_RE.fullmatch(format_id):
            raise ValueError("清晰度格式 ID 无效")
        opts["format"] = f"{format_id}+bestaudio/{format_id}/best"
    elif ffmpeg_location:
        opts["format"] = "bv*+ba/b"
    else:
        opts["format"] = "b[ext=mp4]/b"

    if ffmpeg_location:
        opts.update(
            {
                "merge_output_format": "mp4",
                "ffmpeg_location": ffmpeg_location,
            }
        )
    return opts


def clone_opts(opts: dict) -> dict:
    cloned = dict(opts)
    cloned["http_headers"] = dict(opts.get("http_headers") or {})
    return cloned


def make_attempts(
    job_id: str,
    page_url: str,
    media_url: str,
    fallback_urls: list[str],
    browser: str,
    format_id: str,
) -> list[tuple[str, str, dict]]:
    base_opts = base_ydl_options(job_id, page_url, format_id)
    browser_ok = browser in {"chrome", "edge", "firefox"}
    attempts: list[tuple[str, str, dict]] = []

    def add_target(label: str, target: str, use_cookies: bool) -> None:
        if not target:
            return
        opts = clone_opts(base_opts)
        if use_cookies and browser_ok:
            opts["cookiesfrombrowser"] = (browser,)
        attempts.append((label, target, opts))

    if media_url:
        add_target("captured-media", media_url, False)
        if browser_ok:
            add_target("captured-media-cookies", media_url, True)
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
        add_target(f"captured-fallback-{index}", url, False)
        if browser_ok:
            add_target(f"captured-fallback-{index}-cookies", url, True)

    return attempts


def run_download(job_id: str, payload: dict) -> None:
    try:
        page_url = safe_http_url(payload.get("page_url"))
        media_url = safe_http_url(payload.get("media_url"))
        browser = str(payload.get("browser") or "").strip().lower()
        title_hint = str(payload.get("title") or "").strip()
        format_id = str(payload.get("format_id") or "").strip()

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
            format_id=format_id or None,
            concurrent_fragments=8,
        )

        attempts = make_attempts(job_id, page_url, media_url, fallback_urls, browser, format_id)
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


def format_summary(raw: dict) -> dict:
    height = raw.get("height") or 0
    width = raw.get("width") or 0
    tbr = raw.get("tbr") or 0
    filesize = raw.get("filesize") or raw.get("filesize_approx") or 0
    vcodec = str(raw.get("vcodec") or "")
    acodec = str(raw.get("acodec") or "")
    return {
        "format_id": str(raw.get("format_id") or ""),
        "ext": str(raw.get("ext") or ""),
        "height": int(height) if isinstance(height, (int, float)) else 0,
        "width": int(width) if isinstance(width, (int, float)) else 0,
        "fps": raw.get("fps"),
        "tbr": round(float(tbr), 1) if isinstance(tbr, (int, float)) else 0,
        "filesize": int(filesize) if isinstance(filesize, (int, float)) else 0,
        "protocol": str(raw.get("protocol") or ""),
        "format_note": str(raw.get("format_note") or raw.get("format") or ""),
        "vcodec": vcodec,
        "acodec": acodec,
        "has_video": bool(vcodec and vcodec != "none"),
        "has_audio": bool(acodec and acodec != "none"),
    }


def build_format_list(info: dict) -> list[dict]:
    raw_formats = info.get("formats") or []
    if not isinstance(raw_formats, list):
        raw_formats = []

    result: list[dict] = []
    seen: set[str] = set()
    for raw in raw_formats:
        if not isinstance(raw, dict) or raw.get("has_drm"):
            continue
        item = format_summary(raw)
        format_id = item["format_id"]
        if not format_id or not FORMAT_ID_RE.fullmatch(format_id):
            continue
        if not item["has_video"] and not item["has_audio"]:
            continue
        if format_id in seen:
            continue
        seen.add(format_id)
        result.append(item)

    result.sort(
        key=lambda item: (
            1 if item["has_video"] else 0,
            item["height"],
            item["tbr"],
            item["filesize"],
        ),
        reverse=True,
    )
    return result[:40]


def probe_media(payload: dict) -> dict:
    page_url = safe_http_url(payload.get("page_url"))
    media_url = safe_http_url(payload.get("media_url"))
    target = media_url or page_url
    if not target:
        raise ValueError("缺少待分析地址")

    browser = str(payload.get("browser") or "").strip().lower()
    browser_ok = browser in {"chrome", "edge", "firefox"}
    base_opts = common_ydl_options(page_url)
    base_opts.update({"skip_download": True, "cachedir": False})
    if find_ffmpeg():
        base_opts["ffmpeg_location"] = find_ffmpeg()

    attempts: list[tuple[str, dict]] = []
    if media_url:
        attempts.append(("captured-media", clone_opts(base_opts)))
        if browser_ok:
            cookie_opts = clone_opts(base_opts)
            cookie_opts["cookiesfrombrowser"] = (browser,)
            attempts.append(("captured-media-cookies", cookie_opts))
    else:
        if browser_ok:
            cookie_opts = clone_opts(base_opts)
            cookie_opts["cookiesfrombrowser"] = (browser,)
            attempts.append(("page-extractor-cookies", cookie_opts))
        attempts.append(("page-extractor", clone_opts(base_opts)))

    last_error: Exception | None = None
    for mode, opts in attempts:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False) or {}
            if info.get("_type") == "playlist":
                entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
                if entries:
                    info = entries[0]
            return {
                "ok": True,
                "version": VERSION,
                "platform": detect_platform(page_url or target),
                "strategy": mode,
                "title": str(info.get("title") or payload.get("title") or "视频"),
                "duration": info.get("duration"),
                "thumbnail": str(info.get("thumbnail") or ""),
                "is_live": bool(info.get("is_live")),
                "formats": build_format_list(info),
            }
        except Exception as exc:
            last_error = exc

    raise ValueError(str(last_error or "无法分析这个媒体资源"))


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

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("请求内容无效")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求格式错误")
        return payload

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
                    "strategy": "sniff first + quality probe + extractor fallback",
                    "concurrent_fragments": 8,
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
                    "quality_probe": True,
                    "concurrent_fragments": 8,
                    "formats": ["MP4", "WebM", "M3U8/HLS", "DASH/MPD", "MOV", "M4V", "FLV", "MP3", "M4A"],
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
        try:
            payload = self.read_json()

            if path == "/probe":
                result = probe_media(payload)
                self.send_json(200, result)
                return

            if path != "/download":
                self.send_json(404, {"ok": False, "error": "Not Found"})
                return

            page_url = safe_http_url(payload.get("page_url"))
            media_url = safe_http_url(payload.get("media_url"))
            raw_fallbacks = payload.get("fallback_media_urls") or []
            if not isinstance(raw_fallbacks, list):
                raise ValueError("fallback_media_urls 格式错误")

            format_id = str(payload.get("format_id") or "").strip()
            if format_id and not FORMAT_ID_RE.fullmatch(format_id):
                raise ValueError("清晰度格式 ID 无效")

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
            payload["format_id"] = format_id
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
    print("=" * 66)
    print(f"OmniFetch Local Helper v{VERSION}")
    print(f"Listening : http://{HOST}:{PORT}")
    print(f"Downloads : {DOWNLOAD_DIR}")
    print(f"FFmpeg    : {'available' if find_ffmpeg() else 'not found (single-file fallback)'}")
    print("Fragments : up to 8 concurrent HLS/DASH fragments")
    print("Mode      : sniff first + quality probe + extractor fallback")
    print("Press Ctrl+C to stop.")
    print("=" * 66)

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
