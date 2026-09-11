from dataclasses import dataclass, field

import akshare as ak

from .formatting import dataframe_snapshot


@dataclass(frozen=True)
class SourceBlock:
    title: str
    lines: list[str] = field(default_factory=list)
    error: str | None = None


def fetch_ths_blocks(code: str, name: str, max_news: int) -> list[SourceBlock]:
    blocks = [
        _safe_block("主营介绍", lambda: dataframe_snapshot(ak.stock_zyjs_ths(symbol=code), max_rows=2)),
        _safe_block("财务摘要", lambda: dataframe_snapshot(ak.stock_financial_abstract_ths(symbol=code), max_rows=3)),
        _safe_block("盈利预测", lambda: dataframe_snapshot(ak.stock_profit_forecast_ths(symbol=code), max_rows=3)),
        _safe_block("股东变化", lambda: dataframe_snapshot(ak.stock_shareholder_change_ths(symbol=code), max_rows=3)),
        _safe_block("分红配股", lambda: dataframe_snapshot(ak.stock_fhps_detail_ths(symbol=code), max_rows=3)),
        _safe_block("全球财经资讯匹配", lambda: _filter_global_news(code, name, max_news)),
    ]
    return [block for block in blocks if block.lines or block.error]


def _safe_block(title: str, loader) -> SourceBlock:
    try:
        lines = loader()
        if not lines:
            return SourceBlock(title=title, lines=["暂无数据。"])
        return SourceBlock(title=title, lines=lines)
    except Exception as exc:
        return SourceBlock(title=title, error=str(exc))


def _filter_global_news(code: str, name: str, max_news: int) -> list[str]:
    df = ak.stock_info_global_ths()
    if df.empty:
        return []

    text = df.astype(str).agg(" ".join, axis=1)
    matched = df[text.str.contains(code, case=False, na=False) | text.str.contains(name, case=False, na=False)]
    return dataframe_snapshot(matched, max_rows=max_news)
