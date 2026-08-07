"""RAWG API 客户端，用于按年份获取游戏发售信息。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, Optional

import requests

logger = logging.getLogger(__name__)

RAWG_BASE_URL = "https://api.rawg.io/api"
DEFAULT_PAGE_SIZE = 40  # RAWG API 允许的最大 page_size
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 30


class RawgClient:
    """封装 RAVG（RAWG）API 的客户端，支持按发售日期分页获取游戏列表。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = RAWG_BASE_URL,
        page_size: int = DEFAULT_PAGE_SIZE,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key:
            raise ValueError("RAWG API key 不能为空，请通过 RAWG_API_KEY 环境变量配置。")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.page_size = min(page_size, DEFAULT_PAGE_SIZE)
        self.session = session or requests.Session()

    def iter_games_for_year(self, year: int) -> Iterator[Dict[str, Any]]:
        """按发售年份迭代获取所有游戏，逐页惰性返回。"""
        params = {
            "key": self.api_key,
            "dates": f"{year}-01-01,{year}-12-31",
            "page_size": self.page_size,
            "ordering": "released",
        }
        url: Optional[str] = f"{self.base_url}/games"
        page = 1
        while url:
            logger.info("获取 RAWG 第 %d 页游戏（年份=%d）", page, year)
            response = self._get_with_retry(url, params=params if url == f"{self.base_url}/games" else None)
            payload = response.json()
            for game in payload.get("results", []):
                yield game
            url = payload.get("next")
            page += 1
            params = None  # 后续页直接使用 next 链接已带的查询参数

    def get_games_for_year(self, year: int) -> list[Dict[str, Any]]:
        """一次性返回指定年份的所有游戏列表。"""
        return list(self.iter_games_for_year(year))

    def _get_with_retry(self, url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """带重试的 GET 请求，处理网络错误与 RAWG 速率限制（429）。"""
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    wait_seconds = RETRY_BACKOFF_SECONDS * attempt
                    logger.warning(
                        "RAWG 接口返回 %d，第 %d 次重试前等待 %d 秒",
                        response.status_code,
                        attempt,
                        wait_seconds,
                    )
                    time.sleep(wait_seconds)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                wait_seconds = RETRY_BACKOFF_SECONDS * attempt
                logger.warning(
                    "请求 RAWG 接口出错（第 %d 次）：%s，等待 %d 秒后重试",
                    attempt,
                    exc,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
        raise RuntimeError(f"已重试 {MAX_RETRIES} 次仍无法获取 RAWG 数据：{last_error}")
