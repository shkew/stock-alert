from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from urllib.parse import quote
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .config import OverseasAsset, OverseasConfig, SignalConfig
from .indicators import enrich_indicators, summarize_technical


@dataclass(frozen=True)
class OverseasSnapshot:
    asset: OverseasAsset
    technical: list[str]
    news: list[dict[str, str]]
    error: str | None = None


def build_overseas_sections(overseas: OverseasConfig, signals: SignalConfig) -> list[str]:
    if not overseas.enabled:
        return []

    lines = ["## 外围市场", ""]
    if overseas.indexes:
        lines.extend(["### 指数风向", ""])
        for snapshot in [fetch_overseas_snapshot(asset, overseas.history_days, 0, signals) for asset in overseas.indexes]:
            lines.extend(_render_snapshot(snapshot, include_news=False))

    if overseas.watchlist:
        lines.extend(["### 美股与韩国重点标的", ""])
        for snapshot in [
            fetch_overseas_snapshot(asset, overseas.history_days, overseas.news_limit, signals)
            for asset in overseas.watchlist
        ]:
            lines.extend(_render_snapshot(snapshot, include_news=True))

    return lines


def fetch_overseas_snapshot(
    asset: OverseasAsset,
    history_days: int,
    news_limit: int,
    signals: SignalConfig,
) -> OverseasSnapshot:
    try:
        history = fetch_yahoo_history(asset.symbol, history_days)
        technical = summarize_technical(
            enrich_indicators(history),
            signals.volume_ratio_threshold,
            signals.rsi_overbought,
            signals.rsi_oversold,
        )
    except Exception as exc:
        technical = []
        error = f"行情采集失败：{exc}"
    else:
        error = None

    news = fetch_yahoo_news(asset.symbol, news_limit) if news_limit > 0 else []
    return OverseasSnapshot(asset=asset, technical=technical, news=news, error=error)


def fetch_yahoo_history(symbol: str, days: int) -> pd.DataFrame:
    end = int(time.time())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    encoded = quote(symbol, safe="")
    params = {"period1": start, "period2": end, "interval": "1d", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0"}
    errors: list[str] = []
    response = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}"
        try:
            response = requests.get(url, params=params, timeout=20, headers=headers)
            response.raise_for_status()
            break
        except Exception as exc:
            errors.append(str(exc))
            response = None
    if response is None:
        stooq = _fetch_stooq_history(symbol, days)
        if stooq is not None:
            return stooq
        raise RuntimeError("Yahoo Finance 行情接口失败；" + " | ".join(errors[-2:]))
    payload = response.json()
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote_data = result["indicators"]["quote"][0]
    adjclose = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")

    df = pd.DataFrame(
        {
            "date": pd.to_datetime(timestamps, unit="s").date,
            "open": quote_data.get("open"),
            "high": quote_data.get("high"),
            "low": quote_data.get("low"),
            "close": adjclose or quote_data.get("close"),
            "volume": quote_data.get("volume"),
        }
    )
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if df.empty:
        raise ValueError("Yahoo Finance 未返回可用日线。")

    df["date"] = pd.to_datetime(df["date"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    df["pct_change"] = df["close"].pct_change() * 100
    return df.sort_values("date").reset_index(drop=True)


def _fetch_stooq_history(symbol: str, days: int) -> pd.DataFrame | None:
    stooq_symbol = _stooq_symbol(symbol)
    if not stooq_symbol:
        return None
    url = "https://stooq.com/q/d/l/"
    try:
        response = requests.get(
            url,
            params={"s": stooq_symbol, "i": "d"},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
    except Exception:
        return None
    if df is None or df.empty or "Date" not in df.columns:
        return None

    start = datetime.now() - timedelta(days=days)
    clean = df.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
    clean = clean.dropna(subset=["date", "open", "high", "low", "close"])
    clean = clean[clean["date"] >= start].copy()
    if clean.empty:
        return None
    clean[["open", "high", "low", "close", "volume"]] = clean[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    clean["pct_change"] = clean["close"].pct_change() * 100
    return clean.sort_values("date").reset_index(drop=True)


def _stooq_symbol(symbol: str) -> str:
    mapping = {
        "GLD": "gld.us",
        "QQQ": "qqq.us",
        "SOXX": "soxx.us",
        "AAPL": "aapl.us",
        "NVDA": "nvda.us",
        "TSLA": "tsla.us",
        "^GSPC": "^spx",
        "^IXIC": "^ndq",
        "^DJI": "^dji",
    }
    return mapping.get(symbol, "")


def fetch_yahoo_news(symbol: str, limit: int) -> list[dict[str, str]]:
    encoded = quote(symbol, safe="")
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={encoded}&region=US&lang=en-US"
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception:
        return []

    root = ET.fromstring(response.text)
    news: list[dict[str, str]] = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if title:
            news.append({"title": title, "url": link, "date": pub_date, "source": "Yahoo Finance"})
    return news


def _render_snapshot(snapshot: OverseasSnapshot, include_news: bool) -> list[str]:
    asset = snapshot.asset
    theme_text = f"；主题：{', '.join(asset.themes)}" if asset.themes else ""
    lines = [f"#### {asset.name}（{asset.symbol}，{asset.market}）{theme_text}"]

    if snapshot.error:
        lines.append(f"- {snapshot.error}")
    else:
        lines.extend([f"- {item}" for item in snapshot.technical])

    if include_news:
        if snapshot.news:
            for item in snapshot.news:
                prefix = " / ".join(part for part in (item.get("date"), item.get("source")) if part)
                title = item["title"]
                url = item.get("url", "")
                lines.append(f"- 外围消息：{prefix} [{title}]({url})" if url else f"- 外围消息：{prefix} {title}")
        else:
            lines.append("- 外围消息：暂未获取到相关新闻。")

    lines.append("")
    return lines
