"""共享采集基建：带磁盘缓存、限速、指数退避重试的 HTTP JSON 采集器。

设计要点（面试可讲）：
- 磁盘缓存：同一条 URL 只请求一次，重跑分析不重复打扰数据源；
- 礼貌限速：强制请求最小间隔，尊重 Retry-After；
- 指数退避：网络错误 / 5xx 自动重试，429 按 Retry-After 等待。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests


class Fetcher:
    def __init__(
        self,
        cache_dir: str | Path = ".cache",
        min_interval: float = 1.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.sess = requests.Session()
        self.sess.headers.update(
            headers or {"User-Agent": "data-portfolio/0.1 (study project)"}
        )
        self._last = 0.0

    # —— 内部 ——
    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()[:20]
        return self.cache_dir / f"{h}.json"

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    # —— 对外 ——
    def get_json(self, url: str, params: dict | None = None, refresh: bool = False):
        """GET JSON：先查磁盘缓存，未命中才发请求。"""
        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(params, doseq=True)}"
        cache = self._cache_path(url)
        if cache.exists() and not refresh:
            return json.loads(cache.read_text(encoding="utf-8"))

        err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.sess.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    cache.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                    return data
                if resp.status_code in (301, 302):
                    url = resp.headers["Location"]
                    cache = self._cache_path(url)
                    continue
                if resp.status_code == 429:  # 限流：尊重 Retry-After，缺省等 61s
                    wait = float(resp.headers.get("Retry-After") or 61)
                    print(f"    429 限流，等待 {wait:.0f}s ...")
                    time.sleep(wait)
                    err = RuntimeError(f"429 rate limited: {url}")
                    continue
                if 500 <= resp.status_code < 600:
                    err = RuntimeError(f"{resp.status_code}: {url}")
                    time.sleep(2**attempt)
                    continue
                resp.raise_for_status()
            except requests.RequestException as e:  # 网络层错误
                err = e
                time.sleep(2**attempt)
        raise RuntimeError(f"重试 {self.max_retries} 次后仍失败: {url}") from err


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
