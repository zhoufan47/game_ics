"""RAWG API 客户端，用于按年份获取游戏发售信息。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

RAWG_BASE_URL = "https://api.rawg.io/api"
DEFAULT_PAGE_SIZE = 40  # RAWG API 允许的最大 page_size
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 30

# 代理相关环境变量名。常量集中在此，便于后续维护与单测。
ENV_PROXY = "RAWG_PROXY"          # 同时作用于 http / https 的统一代理
ENV_HTTP_PROXY = "RAWG_HTTP_PROXY"  # 仅作用于 http
ENV_HTTPS_PROXY = "RAWG_HTTPS_PROXY"  # 仅作用于 https


def _read_env(name: str) -> Optional[str]:
    """读取环境变量，同时兼容大写与小写；去除首尾空白后返回。"""
    value = os.environ.get(name) or os.environ.get(name.lower())
    if value is None:
        return None
    value = value.strip()
    return value or None


def resolve_proxies_from_env() -> Dict[str, str]:
    """根据环境变量解析代理配置，未配置则返回空 dict 表示直连。

    优先级：
        1. RAWG_PROXY（同时作为 http / https 代理，最常用）；
        2. RAWG_HTTP_PROXY / RAWG_HTTPS_PROXY（按 scheme 单独配置）。

    Returns:
        形如 ``{"http": "...", "https": "..."}`` 的代理映射；未配置任何代理时为空 dict。
    """
    unified = _read_env(ENV_PROXY)
    http_proxy = _read_env(ENV_HTTP_PROXY) or unified
    https_proxy = _read_env(ENV_HTTPS_PROXY) or unified

    proxies: Dict[str, str] = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies


def _validate_proxy_url(url: str) -> None:
    """粗略校验代理 URL 格式；非法时抛出 ValueError，避免默默配错。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError(
            f"代理 URL 格式无效：{url!r}，期望形如 http://host:port 或 socks5://host:port"
        )


class RawgClient:
    """封装 RAVG（RAWG）API 的客户端，支持按发售日期分页获取游戏列表。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = RAWG_BASE_URL,
        page_size: int = DEFAULT_PAGE_SIZE,
        session: Optional[requests.Session] = None,
        proxies: Optional[Dict[str, str]] = None,
    ) -> None:
        if not api_key:
            raise ValueError("RAWG API key 不能为空，请通过 RAWG_API_KEY 环境变量配置。")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.page_size = min(page_size, DEFAULT_PAGE_SIZE)
        self.session = session or requests.Session()

        # 解析代理：显式传入 > 环境变量 > 直连。
        effective_proxies = proxies if proxies is not None else resolve_proxies_from_env()
        for scheme, proxy_url in effective_proxies.items():
            _validate_proxy_url(proxy_url)
        if effective_proxies:
            # requests 也支持系统级 HTTP_PROXY / HTTPS_PROXY；但本项目要求显式可控，
            # 因此仅在用户配置时写入 session.proxies。
            self.session.proxies.update(effective_proxies)
            logger.info(
                "RAWG 客户端已配置代理：%s",
                ", ".join(f"{k}={v}" for k, v in effective_proxies.items()),
            )

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
