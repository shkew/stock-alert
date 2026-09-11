from datetime import datetime, timedelta
import time

import akshare as ak
import pandas as pd


def fetch_daily_history(code: str, days: int) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days)
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    errors: list[str] = []

    for fetcher in (_fetch_hist_eastmoney, _fetch_hist_sina):
        for attempt in range(3):
            try:
                df = fetcher(code, start_text, end_text)
                return _normalize_history(df)
            except Exception as exc:
                errors.append(f"{fetcher.__name__}: {exc}")
                time.sleep(1 + attempt)

    raise RuntimeError("历史行情接口均失败；" + " | ".join(errors[-3:]))


def _fetch_hist_eastmoney(code: str, start_text: str, end_text: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_text,
        end_date=end_text,
        adjust="qfq",
        timeout=15,
    )


def _fetch_hist_sina(code: str, start_text: str, end_text: str) -> pd.DataFrame:
    prefix = "sh" if code.startswith("6") else "sz"
    return ak.stock_zh_a_daily(
        symbol=f"{prefix}{code}",
        start_date=start_text,
        end_date=end_text,
        adjust="qfq",
    )


def _normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover",
    }
    df = df.rename(columns=rename_map)
    if "date" not in df.columns and "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})

    required = {"date", "open", "close", "high", "low", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"历史行情字段缺失：{', '.join(sorted(missing))}")

    df["date"] = pd.to_datetime(df["date"])
    numeric_cols = [col for col in df.columns if col != "date"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def fetch_stock_news(code: str, limit: int) -> list[dict[str, str]]:
    try:
        news = ak.stock_news_em(symbol=code)
    except Exception:
        return []

    if news.empty:
        return []

    items: list[dict[str, str]] = []
    for _, row in news.head(limit).iterrows():
        title = str(row.get("新闻标题") or row.get("title") or "").strip()
        url = str(row.get("新闻链接") or row.get("url") or "").strip()
        source = str(row.get("文章来源") or row.get("source") or "").strip()
        date = str(row.get("发布时间") or row.get("date") or "").strip()
        if title:
            items.append({"title": title, "url": url, "source": source, "date": date})
    return items
