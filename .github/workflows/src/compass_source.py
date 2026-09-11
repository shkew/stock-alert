from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import pandas as pd
import requests

from .config import CompassConfig
from .formatting import dataframe_snapshot


@dataclass(frozen=True)
class CompassResult:
    source_file: Path | None
    source_name: str
    lines: list[str]
    error: str | None = None


def fetch_compass_data(config: CompassConfig, code: str) -> CompassResult:
    mode = (config.mode or "auto").lower()
    if mode == "off":
        return CompassResult(source_file=None, source_name="disabled", lines=[])

    api_result: CompassResult | None = None
    if mode in {"auto", "api"}:
        api_result = _fetch_compass_api(config, code)
        if api_result.error is None and api_result.lines:
            return api_result
        if mode == "api":
            return api_result

    export_result = fetch_compass_export(config.export_dir, config.file_pattern, code, config.max_rows)
    if mode == "auto" and api_result and api_result.error and _api_was_configured(config) and export_result.error:
        return CompassResult(
            source_file=export_result.source_file,
            source_name=export_result.source_name,
            lines=export_result.lines,
            error=f"指南针 API 未可用：{api_result.error}；导出兜底也失败：{export_result.error}",
        )
    return export_result


def fetch_compass_export(export_dir: Path, file_pattern: str, code: str, max_rows: int) -> CompassResult:
    if not export_dir.exists():
        return CompassResult(
            source_file=None,
            source_name="export",
            lines=[],
            error=f"未找到指南针导出目录：{export_dir}",
        )

    pattern = file_pattern.format(code=code)
    files = sorted(
        [path for path in export_dir.glob(pattern) if path.suffix.lower() in {".csv", ".xlsx", ".xls"}],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return CompassResult(
            source_file=None,
            source_name="export",
            lines=[],
            error=f"未找到 {code} 的指南针导出文件，匹配规则：{export_dir / pattern}",
        )

    source_file = files[0]
    try:
        df = _read_table(source_file)
    except Exception as exc:
        return CompassResult(source_file=source_file, source_name="export", lines=[], error=f"读取失败：{exc}")

    return CompassResult(source_file=source_file, source_name="export", lines=dataframe_snapshot(df, max_rows=max_rows, max_cols=8))


def _fetch_compass_api(config: CompassConfig, code: str) -> CompassResult:
    endpoint_template = os.getenv("COMPASS_API_ENDPOINT_TEMPLATE", config.api_endpoint_template).strip()
    base_url = os.getenv("COMPASS_API_BASE_URL", config.api_base_url).strip()
    token = os.getenv("COMPASS_API_TOKEN", "").strip()
    timeout = int(os.getenv("COMPASS_API_TIMEOUT", str(config.api_timeout or 15)))

    if not endpoint_template:
        return CompassResult(
            source_file=None,
            source_name="api",
            lines=[],
            error="未配置 COMPASS_API_ENDPOINT_TEMPLATE，无法调用指南针 API",
        )

    url = endpoint_template.format(code=code)
    if not url.lower().startswith(("http://", "https://")):
        if not base_url:
            return CompassResult(
                source_file=None,
                source_name="api",
                lines=[],
                error="指南针 API 使用相对路径时需要配置 COMPASS_API_BASE_URL",
            )
        url = urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return CompassResult(source_file=None, source_name="api", lines=[], error=f"API 调用失败：{exc}")

    lines = _snapshot_payload(payload, config.max_rows)
    if not lines:
        return CompassResult(source_file=None, source_name="api", lines=[], error="API 返回为空")
    return CompassResult(source_file=None, source_name=f"api:{_redact_url(url)}", lines=lines)


def _api_was_configured(config: CompassConfig) -> bool:
    return bool(os.getenv("COMPASS_API_ENDPOINT_TEMPLATE", config.api_endpoint_template).strip())


def _snapshot_payload(payload: Any, max_rows: int) -> list[str]:
    data = _extract_tabular_data(payload)
    if isinstance(data, list):
        if not data:
            return []
        if all(isinstance(item, dict) for item in data):
            return dataframe_snapshot(pd.DataFrame(data), max_rows=max_rows, max_cols=8)
        return [str(item) for item in data[:max_rows]]

    if isinstance(data, dict):
        rows = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                continue
            rows.append(f"{key}: {value}")
            if len(rows) >= max_rows:
                break
        return rows

    if data is None:
        return []
    return [str(data)]


def _extract_tabular_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    for key in ("data", "result", "rows", "items", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_tabular_data(value)
            if nested is not None:
                return nested
    return payload


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "...", parts.fragment))


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "gbk", "gb18030"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"不支持的文件类型：{suffix}")
