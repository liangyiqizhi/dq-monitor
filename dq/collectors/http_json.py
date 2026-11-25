"""通用 HTTP JSON 采集器：url + list_path + fields 映射即可接入任意公开接口。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import BaseCollector, CollectorError, register, resolve_path


@register
class HttpJsonCollector(BaseCollector):
    type_name = "http_json"

    def fetch(self) -> list[dict]:
        url = self.cfg.options.get("url")
        if not url:
            raise CollectorError("http_json 源缺少 url 配置")

        timeout = float(self.cfg.options.get("timeout", 10))
        retries = int(self.cfg.options.get("retries", 2))
        headers = {"User-Agent": "dq-monitor", **self.cfg.options.get("headers", {})}

        payload = self._request(url, timeout, retries, headers)
        items = resolve_path(payload, self.cfg.options.get("list_path", ""))
        if not isinstance(items, list):
            raise CollectorError(
                f"list_path={self.cfg.options.get('list_path')!r} 未取到列表，实际类型 {type(items).__name__}"
            )
        return self._map(items)

    def _request(self, url: str, timeout: float, retries: int, headers: dict) -> object:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
                last_err = err
                if attempt < retries:
                    time.sleep(2**attempt)  # 指数退避
        raise CollectorError(f"请求失败（已重试 {retries} 次）：{type(last_err).__name__}: {last_err}")

    def _map(self, items: list) -> list[dict]:
        mapping: dict[str, str] = self.cfg.options.get("fields") or {}
        records: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not mapping:
                records.append(item)
            else:
                records.append({target: resolve_path(item, src) for target, src in mapping.items()})
        return records
