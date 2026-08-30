from __future__ import annotations

import json
import os
import shutil
import subprocess
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
DOWNLOAD_DIR = Path.home() / "Downloads" / "OmniFetch"
TOOLS_DIR = Path(__file__).resolve().parent / "tools"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


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
    return value


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


def base_ydl_options(job_id: str, page_url: str, title_hint: str) -> dict:
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
        # 没有 FFmpeg 时优先选择已经包含音视频的单文件格式。
        opts["format"] = "b[ext=mp4]/b"

    return opts


def run_download(job_id: str, payload: dict) -> None:
    try:
        page_url = safe_http_url(payload.get("page_url"))
        media_url = safe_http_url(payload.get("media_url"))
        target_url = media_url or page_url
        if not target_url:
            raise ValueError("缺少 page_url 或 media_url")

        title_hint = str(payload.get("title") or "").strip()
        browser = str(payload.get("browser") or "").strip().lower()

        update_job(job_id, status="starting", target_url=target_url)
        opts = base_ydl_options(job_id, page_url, title_hint)

        # 对页面地址优先尝试读取用户自己的浏览器登录状态。
        # 如果浏览器 Cookie 数据库被占用或读取失败，会自动无 Cookie 重试。
        use_browser_cookies = bool(page_url and not media_url and browser in {"chrome", "edge", "firefox"})

        attempts: list[tuple[str, dict]] = []
        if use_browser_cookies:
            cookie_opts = dict(opts)
            cookie_opts["cookiesfrombrowser"] = (browser,)
            attempts.append(("browser-cookies", cookie_opts))
        attempts.append(("plain", opts))

        last_error: Exception | None = None
        for index, (mode, ydl_opts) in enumerate(attempts):
            update_job(job_id, status="resolving", mode=mode)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    final_title = (info or {}).get("title") or title_hint or "video"
                    final_id = (info or {}).get("id") or ""
                    update_job(
                        job_id,
                        status="completed",
                        percent=100,
                        title=final_title,
                        media_id=final_id,
                        output_dir=str(DOWNLOAD_DIR),
                        error=None,
                    )
                    return
            except Exception as exc:
                last_error = exc
                if index + 1 < len(attempts):
                    update_job(job_id, status="retrying", error=str(exc))
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
    server_version = "OmniFetchHelper/0.1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
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
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "OmniFetch Helper",
                    "version": "0.1.0",
                    "download_dir": str(DOWNLOAD_DIR),
                    "ffmpeg": bool(find_ffmpeg()),
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
            if not page_url and not media_url:
                raise ValueError("缺少 page_url 或 media_url")

            payload["page_url"] = page_url
            payload["media_url"] = media_url
            job_id = create_job(payload)
            self.send_json(
                202,
                {
                    "ok": True,
                    "job_id": job_id,
                    "status": "queued",
                    "output_dir": str(DOWNLOAD_DIR),
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except json.JSONDecodeError:
            self.send_json(400, {"ok": False, "error": "JSON 格式错误"})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 62)
    print("OmniFetch Local Helper v0.1.0")
    print(f"Listening : http://{HOST}:{PORT}")
    print(f"Downloads : {DOWNLOAD_DIR}")
    print(f"FFmpeg    : {'available' if find_ffmpeg() else 'not found (single-file fallback)'}")
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
