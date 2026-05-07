"""FastAPI REST 服务入口。

HTTP 层薄封装：
- 接收 URL，创建 job，返回 job_id
- 实际下载委托给 cli.main.download_url 的简化复用

fastapi/uvicorn 是**可选**依赖。若未安装，导入本模块会 ImportError。
"""

from __future__ import annotations

import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import DouyinAPIClient, URLParser, DownloaderFactory
from core.downloader_base import BaseDownloader
from core.video_downloader import VideoDownloader
from server.jobs import JobManager
from storage import FileManager
from utils.logger import setup_logger
from utils.validators import is_short_url, normalize_short_url, sanitize_filename

logger = setup_logger("REST")

_PASTED_URL_RE = re.compile(
    r"(https?://[^\s<>'\"]+|(?:www\.)?douyin\.com/[^\s<>'\"]+|"
    r"(?:v\.douyin\.com|v\.iesdouyin\.com|iesdouyin\.com)/[^\s<>'\"]+)",
    re.IGNORECASE,
)
_TRAILING_URL_CHARS = "，。！？；、,.;!?)）]】}>\"'"


class DownloadRequest(BaseModel):
    url: str


class ParseRequest(BaseModel):
    url: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    url: str


class MediaEntry:
    def __init__(
        self,
        *,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        filename: str = "media.bin",
        content_type: str = "application/octet-stream",
        expires_at: float,
    ):
        self.url = url
        self.headers = headers or {}
        self.filename = filename
        self.content_type = content_type
        self.expires_at = expires_at


class MediaCache:
    """Short-lived in-memory mapping from opaque media ids to remote assets."""

    DEFAULT_TTL_SECONDS = 30 * 60

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self._items: Dict[str, MediaEntry] = {}

    def register(
        self,
        *,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        filename: str = "media.bin",
        content_type: str = "application/octet-stream",
    ) -> str:
        self._prune()
        media_id = uuid.uuid4().hex
        self._items[media_id] = MediaEntry(
            url=url,
            headers=headers,
            filename=filename,
            content_type=content_type,
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        return media_id

    def get(self, media_id: str) -> Optional[MediaEntry]:
        entry = self._items.get(media_id)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._items.pop(media_id, None)
            return None
        return entry

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            media_id
            for media_id, entry in self._items.items()
            if entry.expires_at <= now
        ]
        for media_id in expired:
            self._items.pop(media_id, None)


class _ServerDeps:
    """跨请求复用的重量级依赖。

    REST 服务在进程生命周期内只需要一份 FileManager / RateLimiter / RetryHandler /
    QueueManager / CookieManager；每个请求重新构造既浪费又会触发文件系统 mkdir。
    DouyinAPIClient 由于持有 aiohttp.ClientSession，依旧按请求创建，避免跨请求泄漏
    连接状态或触发 "Session is closed" 错误。
    """

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.cookie_manager = CookieManager()
        self.cookie_manager.set_cookies(config.get_cookies())
        self.file_manager = FileManager(config.get("path"))
        self.rate_limiter = RateLimiter(
            max_per_second=float(config.get("rate_limit", 2) or 2)
        )
        self.retry_handler = RetryHandler(
            max_retries=int(config.get("retry_times", 3) or 3)
        )
        self.queue_manager = QueueManager(
            max_workers=int(config.get("thread", 5) or 5)
        )


async def _execute_download(url: str, deps: "_ServerDeps") -> Dict[str, int]:
    """简化版 download_url：只负责执行并返回成功/失败计数。

    有意不复用 cli.main.download_url —— 后者绑定了 progress_display 的 rich 状态。
    API client 仍按请求创建（aiohttp session 不跨请求复用）；其余重量级依赖从
    _ServerDeps 共享。
    """
    async with DouyinAPIClient(
        deps.cookie_manager.get_cookies(),
        proxy=deps.config.get("proxy"),
    ) as api_client:
        if is_short_url(url):
            resolved = await api_client.resolve_short_url(normalize_short_url(url))
            if not resolved:
                raise RuntimeError(f"Failed to resolve short URL: {url}")
            url = resolved

        parsed = URLParser.parse(url)
        if not parsed:
            raise RuntimeError(f"Unsupported URL: {url}")

        downloader = DownloaderFactory.create(
            parsed["type"],
            deps.config,
            api_client,
            deps.file_manager,
            deps.cookie_manager,
            None,  # database 不在 server 场景里启用，避免单例冲突
            deps.rate_limiter,
            deps.retry_handler,
            deps.queue_manager,
            progress_reporter=None,
        )
        if downloader is None:
            raise RuntimeError(f"No downloader for url_type={parsed['type']}")

        result = await downloader.download(parsed)
        return {
            "total": result.total,
            "success": result.success,
            "failed": result.failed,
            "skipped": result.skipped,
        }


def _extract_url_from_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    match = _PASTED_URL_RE.search(text)
    if match:
        text = match.group(1)
    text = text.strip().strip(_TRAILING_URL_CHARS)
    if re.match(r"^(?:www\.)?douyin\.com/", text, flags=re.IGNORECASE):
        return f"https://{text}"
    return text


def _media_path(media_id: str, *, download: bool = False) -> str:
    path = f"/api/v1/media/{media_id}"
    if download:
        return f"{path}?download=1"
    return path


async def _parse_video_for_preview(
    raw_url: str,
    deps: "_ServerDeps",
    media_cache: MediaCache,
) -> Dict[str, Any]:
    url = _extract_url_from_text(raw_url)
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    async with DouyinAPIClient(
        deps.cookie_manager.get_cookies(),
        proxy=deps.config.get("proxy"),
    ) as api_client:
        if is_short_url(url):
            resolved = await api_client.resolve_short_url(normalize_short_url(url))
            if not resolved:
                raise HTTPException(status_code=400, detail="failed to resolve short url")
            url = resolved

        parsed = URLParser.parse(url)
        if not parsed or parsed.get("type") == "short":
            raise HTTPException(status_code=400, detail="unsupported douyin url")
        if parsed.get("type") != "video":
            raise HTTPException(status_code=400, detail="only video urls are supported")

        aweme_id = parsed.get("aweme_id")
        if not aweme_id:
            raise HTTPException(status_code=400, detail="missing aweme_id")

        await deps.rate_limiter.acquire()
        aweme_data = await api_client.get_video_detail(str(aweme_id))
        if not aweme_data:
            raise HTTPException(status_code=502, detail="failed to fetch video detail")

        helper = VideoDownloader(
            deps.config,
            api_client,
            deps.file_manager,
            deps.cookie_manager,
            None,
            deps.rate_limiter,
            deps.retry_handler,
            deps.queue_manager,
            progress_reporter=None,
        )
        if helper._detect_media_type(aweme_data) != "video":
            raise HTTPException(status_code=400, detail="the parsed item is not a video")

        video_info = helper._build_no_watermark_url(aweme_data)
        if not video_info:
            raise HTTPException(status_code=502, detail="no playable video url found")

        video_url, video_headers = video_info
        video = aweme_data.get("video") if isinstance(aweme_data.get("video"), dict) else {}
        author = (
            aweme_data.get("author")
            if isinstance(aweme_data.get("author"), dict)
            else {}
        )
        title = (aweme_data.get("desc") or "douyin_video").strip() or "douyin_video"
        safe_stem = sanitize_filename(f"{title}_{aweme_id}", max_length=120)
        video_id = media_cache.register(
            url=video_url,
            headers=video_headers,
            filename=f"{safe_stem}.mp4",
            content_type="video/mp4",
        )

        cover_url = BaseDownloader._extract_first_url(video.get("cover"))
        cover_path = ""
        if cover_url:
            cover_id = media_cache.register(
                url=cover_url,
                headers=helper._download_headers(),
                filename=f"{safe_stem}_cover.jpg",
                content_type="image/jpeg",
            )
            cover_path = _media_path(cover_id)

        avatar_url = BaseDownloader._extract_first_url(author.get("avatar_larger"))
        avatar_path = ""
        if avatar_url:
            avatar_id = media_cache.register(
                url=avatar_url,
                headers=helper._download_headers(),
                filename=f"{safe_stem}_avatar.jpg",
                content_type="image/jpeg",
            )
            avatar_path = _media_path(avatar_id)

        return {
            "aweme_id": str(aweme_id),
            "original_url": raw_url,
            "resolved_url": url,
            "title": title,
            "create_time": aweme_data.get("create_time"),
            "duration": video.get("duration") or 0,
            "author": {
                "uid": str(author.get("uid") or ""),
                "sec_uid": str(author.get("sec_uid") or ""),
                "nickname": str(author.get("nickname") or ""),
                "avatar_url": avatar_path,
            },
            "video": {
                "preview_url": _media_path(video_id),
                "download_url": _media_path(video_id, download=True),
                "filename": f"{safe_stem}.mp4",
            },
            "cover_url": cover_path,
        }


def build_app(config: ConfigLoader) -> FastAPI:
    deps = _ServerDeps(config)

    async def executor(url: str) -> Dict[str, int]:
        return await _execute_download(url, deps)

    server_cfg = config.get("server") or {}
    if not isinstance(server_cfg, dict):
        server_cfg = {}
    manager = JobManager(
        executor=executor,
        max_concurrency=int(config.get("thread", 2) or 2),
        max_jobs=int(server_cfg.get("max_jobs") or JobManager.DEFAULT_MAX_JOBS),
        job_ttl_seconds=float(
            server_cfg.get("job_ttl_seconds") or JobManager.DEFAULT_JOB_TTL_SECONDS
        ),
    )
    media_cache = MediaCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await manager.shutdown()

    app = FastAPI(
        title="Douyin Downloader API",
        version="1.0",
        description="REST API for dispatching Douyin download jobs.",
        lifespan=lifespan,
    )
    app.state.job_manager = manager
    app.state.media_cache = media_cache
    app.state.deps = deps

    @app.get("/api/v1/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/parse")
    async def parse_video(req: ParseRequest) -> Dict[str, Any]:
        return await _parse_video_for_preview(req.url, deps, media_cache)

    @app.get("/api/v1/media/{media_id}")
    async def proxy_media(
        media_id: str,
        request: Request,
        download: int = Query(0),
    ) -> StreamingResponse:
        entry = media_cache.get(media_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="media not found or expired")

        headers = dict(entry.headers)
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header

        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
        )
        try:
            upstream = await session.get(
                entry.url,
                headers=headers,
                proxy=deps.config.get("proxy") or None,
            )
        except Exception as exc:
            await session.close()
            raise HTTPException(status_code=502, detail=f"media fetch failed: {exc}")

        if upstream.status >= 400:
            status = upstream.status
            upstream.close()
            await session.close()
            raise HTTPException(status_code=status, detail="upstream media fetch failed")

        response_headers: Dict[str, str] = {}
        for name in ("content-length", "content-range", "accept-ranges"):
            value = upstream.headers.get(name)
            if value:
                response_headers[name] = value
        if download:
            response_headers[
                "content-disposition"
            ] = f"attachment; filename*=UTF-8''{quote(entry.filename)}"

        content_type = upstream.headers.get("content-type") or entry.content_type

        async def stream_body():
            try:
                async for chunk in upstream.content.iter_chunked(256 * 1024):
                    yield chunk
            finally:
                upstream.close()
                await session.close()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status,
            media_type=content_type,
            headers=response_headers,
        )

    @app.post("/api/v1/download", response_model=JobResponse)
    async def create_job(req: DownloadRequest) -> JobResponse:
        if not req.url:
            raise HTTPException(status_code=400, detail="url is required")
        job = await manager.submit(req.url)
        return JobResponse(job_id=job.job_id, status=job.status, url=job.url)

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> Dict[str, Any]:
        job = await manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @app.get("/api/v1/jobs")
    async def list_jobs() -> Dict[str, List[Dict[str, Any]]]:
        jobs = await manager.list_jobs()
        return {"jobs": [j.to_dict() for j in jobs]}

    return app


async def run_server(config: ConfigLoader, *, host: str, port: int) -> None:
    import uvicorn

    app = build_app(config)
    uv_config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(uv_config)
    await server.serve()
