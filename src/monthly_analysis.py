from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isnan
from typing import Any

import akshare as ak
import pandas as pd

from .config import AppConfig


@dataclass(frozen=True)
class MarketBoard:
    name: str
    board_type: str
    score: float
    pct_change: float
    amount: float
    turnover: float
    leader_name: str
    leader_price: float
    leader_pct_change: float
    reason: str


@dataclass(frozen=True)
class MarketIndex:
    code: str
    name: str
    score: float
    close: float
    pct_change: float
    month_return: float
    ma20_gap: float
    ma60_gap: float
    volume_ratio: float
    state: str
    reason: str


@dataclass(frozen=True)
class MarketCandidate:
    code: str
    name: str
    board: str
    board_type: str
    score: float
    pct_change: float
    amount: float
    turnover: float
    price: float
    revenue_growth_pct: float | None
    revenue_period: str
    reason: str


@dataclass(frozen=True)
class MonthlyAnalysis:
    generated_at: datetime
    indexes: list[MarketIndex]
    market_score: float
    market_state: str
    boards: list[MarketBoard]
    candidates: list[MarketCandidate]
    errors: list[str]


def build_monthly_analysis(config: AppConfig) -> MonthlyAnalysis:
    errors: list[str] = []
    indexes = _fetch_market_indexes(config, errors)
    market_score, market_state = _market_environment(indexes)
    boards = _fetch_hot_boards(config, errors)
    candidates = _fetch_market_candidates(config, boards, market_score, errors)
    return MonthlyAnalysis(
        generated_at=datetime.now(),
        indexes=indexes,
        market_score=market_score,
        market_state=market_state,
        boards=boards,
        candidates=candidates,
        errors=errors,
    )


def render_monthly_report(config: AppConfig) -> str:
    analysis = build_monthly_analysis(config)
    lines = [
        f"# 全市场板块扫描与个股候选 {analysis.generated_at:%Y-%m-%d}",
        "",
        f"生成时间：{analysis.generated_at:%Y-%m-%d %H:%M:%S}",
        "筛选范围：上证指数 + 深证成指 + 科创50 + 东方财富行业/概念板块，不局限于自选股。",
        "",
        "> 说明：本报告用大盘环境、板块涨幅、成交额、换手率、成分股强度和营收增长做市场扫描，仅作研究和复盘参考，不构成投资建议。",
        "",
    ]

    lines.extend(_render_index_section(analysis))
    lines.extend(_render_board_section(analysis))
    lines.extend(_render_candidate_section(config, analysis))
    lines.extend(_render_error_section(analysis))
    return "\n".join(lines).strip() + "\n"


def save_monthly_report(config: AppConfig) -> str:
    markdown = render_monthly_report(config)
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"market_scan_{datetime.now():%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return markdown


def _fetch_market_indexes(config: AppConfig, errors: list[str]) -> list[MarketIndex]:
    indexes: list[MarketIndex] = []
    for code in config.monthly.indexes:
        try:
            history = _fetch_index_history(code, max(config.monthly.lookback_days + 90, 120))
            indexes.append(_analyze_index(code, history, config.monthly.lookback_days))
        except Exception as exc:
            try:
                indexes.append(_fetch_index_spot_fallback(code))
                errors.append(f"{_index_name(code)}（{code}）历史趋势失败，已使用实时指数兜底：{exc}")
            except Exception as fallback_exc:
                errors.append(f"{_index_name(code)}（{code}）指数采集失败：{exc}；实时兜底失败：{fallback_exc}")
    return indexes


def _fetch_index_spot_fallback(code: str) -> MarketIndex:
    df = ak.stock_zh_index_spot_em()
    if df is None or df.empty:
        raise ValueError("指数实时行情为空")

    target = code[-6:]
    row = None
    for item in df.fillna("").to_dict("records"):
        item_code = str(item.get("代码", item.get("code", ""))).zfill(6)
        item_name = str(item.get("名称", item.get("name", ""))).strip()
        if item_code == target or item_name == _index_name(code):
            row = item
            break
    if row is None:
        raise ValueError(f"指数实时行情未找到 {code}")

    close = _to_float(row.get("最新价", row.get("close", 0)))
    pct_change = _to_float(row.get("涨跌幅", row.get("pct_change", 0)))
    volume = _to_float(row.get("成交量", row.get("volume", 0)))
    score, state, reason = _score_spot_index(pct_change)
    return MarketIndex(
        code=code,
        name=_index_name(code),
        score=score,
        close=close,
        pct_change=pct_change,
        month_return=0,
        ma20_gap=0,
        ma60_gap=0,
        volume_ratio=0 if volume <= 0 else 1,
        state=state,
        reason=reason,
    )


def _score_spot_index(pct_change: float) -> tuple[float, str, str]:
    score = round(_clamp(50 + pct_change * 8, 0, 100), 1)
    if pct_change >= 1:
        return score, "当日偏强", "实时指数上涨"
    if pct_change >= 0:
        return score, "当日震荡偏强", "实时指数小幅上涨"
    if pct_change > -1:
        return score, "当日震荡偏弱", "实时指数小幅下跌"
    return score, "当日偏弱", "实时指数下跌"


def _fetch_index_history(code: str, days: int) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days)
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    errors: list[str] = []

    for loader in (
        lambda: ak.index_zh_a_hist(symbol=code, period="daily", start_date=start_text, end_date=end_text),
        lambda: ak.stock_zh_index_daily_em(symbol=code),
        lambda: ak.stock_zh_index_daily(symbol=_index_symbol(code)),
    ):
        try:
            df = loader()
            return _normalize_index_history(df, start)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("指数行情接口均失败；" + " | ".join(errors[-2:]))


def _normalize_index_history(df: pd.DataFrame, start: datetime) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("指数历史为空")
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_change",
        "date": "date",
        "open": "open",
        "close": "close",
        "high": "high",
        "low": "low",
        "volume": "volume",
        "amount": "amount",
    }
    clean = df.rename(columns=rename_map).copy()
    required = {"date", "close"}
    missing = required.difference(clean.columns)
    if missing:
        raise ValueError(f"指数行情字段缺失：{', '.join(sorted(missing))}")
    clean["date"] = pd.to_datetime(clean["date"])
    numeric_cols = [col for col in clean.columns if col != "date"]
    clean[numeric_cols] = clean[numeric_cols].apply(pd.to_numeric, errors="coerce")
    clean = clean[clean["date"] >= start].sort_values("date").reset_index(drop=True)
    if "volume" not in clean.columns:
        clean["volume"] = 0
    if "pct_change" not in clean.columns:
        clean["pct_change"] = clean["close"].pct_change() * 100
    return clean


def _analyze_index(code: str, history: pd.DataFrame, lookback_days: int) -> MarketIndex:
    if len(history) < 60:
        raise ValueError("指数历史不足 60 个交易日")
    close = history["close"]
    latest = history.iloc[-1]
    first = history.tail(lookback_days + 1).iloc[0]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma20_gap = float(latest["close"] / ma20 * 100 - 100)
    ma60_gap = float(latest["close"] / ma60 * 100 - 100)
    month_return = float(latest["close"] / first["close"] * 100 - 100)
    volume_ratio = _safe_ratio(history["volume"].tail(5).mean(), history["volume"].tail(20).mean())
    pct_change = float(latest.get("pct_change", 0))
    score, state, reason = _score_index(month_return, ma20_gap, ma60_gap, pct_change, volume_ratio)
    return MarketIndex(
        code=code,
        name=_index_name(code),
        score=score,
        close=float(latest["close"]),
        pct_change=pct_change,
        month_return=month_return,
        ma20_gap=ma20_gap,
        ma60_gap=ma60_gap,
        volume_ratio=volume_ratio,
        state=state,
        reason=reason,
    )


def _score_index(month_return: float, ma20_gap: float, ma60_gap: float, pct_change: float, volume_ratio: float) -> tuple[float, str, str]:
    score = 50
    score += _clamp(month_return * 2, -15, 18)
    score += 10 if ma20_gap > 0 else -8
    score += 12 if ma60_gap > 0 else -10
    score += _clamp(pct_change * 3, -8, 8)
    if pct_change < 0 and volume_ratio >= 1.2:
        score -= 8
    elif pct_change > 0 and volume_ratio >= 1.1:
        score += 5

    score = round(_clamp(score, 0, 100), 1)
    if score >= 70:
        state = "风险偏好较强"
    elif score >= 55:
        state = "震荡偏强"
    elif score >= 40:
        state = "震荡偏弱"
    else:
        state = "风险偏好偏弱"

    reasons = []
    reasons.append("站上 MA20" if ma20_gap > 0 else "低于 MA20")
    reasons.append("站上 MA60" if ma60_gap > 0 else "低于 MA60")
    if month_return > 0:
        reasons.append("月线为正")
    else:
        reasons.append("月线承压")
    if pct_change < 0 and volume_ratio >= 1.2:
        reasons.append("放量下跌")
    return score, state, "；".join(reasons)


def _market_environment(indexes: list[MarketIndex]) -> tuple[float, str]:
    if not indexes:
        return 50.0, "大盘数据缺失，候选股仅按板块和个股强度筛选"
    score = round(sum(index.score for index in indexes) / len(indexes), 1)
    if score >= 70:
        return score, "大盘环境偏强，可提高强板块跟踪优先级"
    if score >= 55:
        return score, "大盘震荡偏强，适合控制仓位做板块轮动"
    if score >= 40:
        return score, "大盘震荡偏弱，候选股需要更严格等待确认"
    return score, "大盘风险偏好偏弱，应优先防守和控制回撤"


def _fetch_hot_boards(config: AppConfig, errors: list[str]) -> list[MarketBoard]:
    frames: list[pd.DataFrame] = []
    for board_type in config.monthly.board_types:
        try:
            df = _fetch_board_frame(board_type)
        except Exception as exc:
            errors.append(f"{_board_type_label(board_type)}板块列表采集失败：{exc}")
            continue
        if not df.empty:
            df = df.copy()
            df["board_type"] = board_type
            frames.append(df)

    if not frames:
        return []

    merged = pd.concat(frames, ignore_index=True)
    boards: list[MarketBoard] = []
    for _, row in merged.iterrows():
        name = _first_text(row, ["板块名称", "名称"])
        if not name:
            continue
        pct_change = _first_number(row, ["涨跌幅", "涨幅", "涨跌幅%"])
        amount = _first_number(row, ["成交额", "成交金额"])
        turnover = _first_number(row, ["换手率"])
        leader_name = _first_text(row, ["领涨股"])
        leader_price = _first_number(row, ["领涨股-最新价"])
        leader_pct_change = _first_number(row, ["领涨股-涨跌幅"])
        score = _score_board(pct_change, amount, turnover)
        boards.append(
            MarketBoard(
                name=name,
                board_type=str(row.get("board_type", "")),
                score=score,
                pct_change=pct_change,
                amount=amount,
                turnover=turnover,
                leader_name=leader_name,
                leader_price=leader_price,
                leader_pct_change=leader_pct_change,
                reason=_board_reason(pct_change, amount, turnover),
            )
        )

    boards = [board for board in boards if board.pct_change > 0]
    return sorted(boards, key=lambda item: item.score, reverse=True)[: config.monthly.max_boards_to_scan]


def _fetch_market_candidates(config: AppConfig, boards: list[MarketBoard], market_score: float, errors: list[str]) -> list[MarketCandidate]:
    candidates: dict[str, MarketCandidate] = {}
    for board in boards[: config.monthly.max_boards_to_scan]:
        try:
            df = _fetch_board_constituents(board)
        except Exception as exc:
            errors.append(f"{board.name} 成分股采集失败：{exc}")
            leader = _leader_candidate(board, market_score)
            if leader:
                candidates[f"leader:{board.name}:{leader.name}"] = leader
            continue
        if df.empty:
            continue

        ranked = _rank_constituents(df, board, config, market_score)
        for candidate in ranked[: config.monthly.stocks_per_board]:
            existing = candidates.get(candidate.code)
            if existing is None or candidate.score > existing.score:
                candidates[candidate.code] = candidate

    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)[: config.monthly.top_n]


def _fetch_board_frame(board_type: str) -> pd.DataFrame:
    if board_type == "industry":
        try:
            return ak.stock_board_industry_name_em()
        except Exception:
            return _normalize_ths_industry_summary(ak.stock_board_industry_summary_ths())
    if board_type == "concept":
        return ak.stock_board_concept_name_em()
    raise ValueError(f"不支持的板块类型：{board_type}")


def _fetch_board_constituents(board: MarketBoard) -> pd.DataFrame:
    if board.board_type == "industry":
        return ak.stock_board_industry_cons_em(symbol=board.name)
    if board.board_type == "concept":
        return ak.stock_board_concept_cons_em(symbol=board.name)
    raise ValueError(f"不支持的板块类型：{board.board_type}")


def _rank_constituents(df: pd.DataFrame, board: MarketBoard, config: AppConfig, market_score: float) -> list[MarketCandidate]:
    items: list[MarketCandidate] = []
    for _, row in df.iterrows():
        code = _first_text(row, ["代码", "股票代码"])
        name = _first_text(row, ["名称", "股票名称"])
        if not code or not name:
            continue
        pct_change = _first_number(row, ["涨跌幅", "涨幅", "涨跌幅%"])
        amount = _first_number(row, ["成交额", "成交金额"])
        turnover = _first_number(row, ["换手率"])
        price = _first_number(row, ["最新价", "收盘", "价格"])
        if amount < config.monthly.min_amount or pct_change <= 0:
            continue
        revenue = _fetch_revenue_growth(code)
        if config.monthly.require_revenue_growth and not _passes_revenue_check(revenue, config):
            continue
        score = _score_candidate(board, pct_change, amount, turnover, revenue.growth_pct, market_score)
        items.append(
            MarketCandidate(
                code=code.zfill(6),
                name=name,
                board=board.name,
                board_type=board.board_type,
                score=score,
                pct_change=pct_change,
                amount=amount,
                turnover=turnover,
                price=price,
                revenue_growth_pct=revenue.growth_pct,
                revenue_period=revenue.period,
                reason=_candidate_reason(board, pct_change, amount, turnover, revenue),
            )
        )
    return sorted(items, key=lambda item: item.score, reverse=True)


def _leader_candidate(board: MarketBoard, market_score: float) -> MarketCandidate | None:
    if not board.leader_name:
        return None
    score = round(_clamp(board.score * 0.55 + 30 + _clamp(board.leader_pct_change * 2, 0, 18) + _clamp((market_score - 50) * 0.2, -8, 8), 0, 100), 1)
    return MarketCandidate(
        code="待确认",
        name=board.leader_name,
        board=board.name,
        board_type=board.board_type,
        score=score,
        pct_change=board.leader_pct_change,
        amount=board.amount,
        turnover=0,
        price=board.leader_price,
        revenue_growth_pct=None,
        revenue_period="",
        reason=f"{board.name}领涨股；板块强度 {board.score:.1f}；代码和财务需二次确认",
    )


def _normalize_ths_industry_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    clean = df.copy()
    if "板块" in clean.columns:
        clean["板块名称"] = clean["板块"]
    if "总成交额" in clean.columns:
        clean["成交额"] = pd.to_numeric(clean["总成交额"], errors="coerce").fillna(0) * 100_000_000
    if "涨跌幅" in clean.columns:
        clean["涨跌幅"] = pd.to_numeric(clean["涨跌幅"], errors="coerce").fillna(0)
    if "换手率" not in clean.columns:
        clean["换手率"] = 0
    return clean


def _score_board(pct_change: float, amount: float, turnover: float) -> float:
    score = 50
    score += _clamp(pct_change * 5, -15, 25)
    score += _clamp(amount / 10_000_000_000 * 12, 0, 18)
    score += _clamp(turnover * 1.5, 0, 12)
    return round(_clamp(score, 0, 100), 1)


@dataclass(frozen=True)
class RevenueGrowth:
    growth_pct: float | None
    period: str
    source: str
    error: str | None = None


def _score_candidate(
    board: MarketBoard,
    pct_change: float,
    amount: float,
    turnover: float,
    revenue_growth_pct: float | None,
    market_score: float,
) -> float:
    score = board.score * 0.35 + 35
    score += _clamp((market_score - 50) * 0.25, -8, 8)
    score += _clamp(pct_change * 4, 0, 25)
    score += _clamp(amount / 1_000_000_000 * 8, 0, 18)
    score += _clamp(turnover * 1.2, 0, 12)
    if revenue_growth_pct is not None:
        score += _clamp(revenue_growth_pct / 5, -10, 12)
    if pct_change >= 9.5:
        score -= 6
    if turnover >= 25:
        score -= 8
    return round(_clamp(score, 0, 100), 1)


def _board_reason(pct_change: float, amount: float, turnover: float) -> str:
    reasons = []
    if pct_change >= 3:
        reasons.append("板块涨幅靠前")
    elif pct_change > 0:
        reasons.append("板块上涨")
    if amount >= 20_000_000_000:
        reasons.append("成交额活跃")
    if turnover >= 4:
        reasons.append("换手率较高")
    return "；".join(reasons) if reasons else "市场热度一般"


def _candidate_reason(board: MarketBoard, pct_change: float, amount: float, turnover: float, revenue: RevenueGrowth) -> str:
    reasons = [f"所属{_board_type_label(board.board_type)}板块强度 {board.score:.1f}"]
    if revenue.growth_pct is not None:
        reasons.append(f"{revenue.period}营收同比 {revenue.growth_pct:.2f}%")
    elif revenue.error:
        reasons.append(f"营收数据未确认：{revenue.error}")
    if pct_change >= 5:
        reasons.append("个股涨幅强")
    if amount >= 1_000_000_000:
        reasons.append("成交额过 10 亿")
    if turnover >= 5:
        reasons.append("换手活跃")
    if pct_change >= 9.5:
        reasons.append("接近涨停，注意追高风险")
    if turnover >= 25:
        reasons.append("高换手，分歧较大")
    return "；".join(reasons)


def _render_index_section(analysis: MonthlyAnalysis) -> list[str]:
    lines = ["## 大盘环境", ""]
    lines.append(f"- 综合判断：{analysis.market_state}；大盘环境分 {analysis.market_score:.1f}。")
    if not analysis.indexes:
        lines.append("- 指数数据暂不可用。")
        lines.append("")
        return lines

    for index in analysis.indexes:
        lines.append(
            f"- {index.name}（{index.code}）：{index.state}，评分 {index.score:.1f}；"
            f"收盘 {index.close:.2f}，日涨跌幅 {_pct(index.pct_change)}，"
            f"近 {index.name}观察月涨跌幅 {_pct(index.month_return)}，"
            f"距 MA20 {_pct(index.ma20_gap)}，距 MA60 {_pct(index.ma60_gap)}。理由：{index.reason}。"
        )
    lines.append("")
    return lines


def _render_board_section(analysis: MonthlyAnalysis) -> list[str]:
    lines = ["## 市场强势板块", ""]
    if not analysis.boards:
        return lines + ["- 暂无可用板块数据，可能是公开数据接口暂时不可用。", ""]

    for board in analysis.boards[:10]:
        lines.append(
            f"- {board.name}（{_board_type_label(board.board_type)}）：评分 {board.score:.1f}；"
            f"涨跌幅 {_pct(board.pct_change)}，成交额 {_money(board.amount)}，换手率 {_pct(board.turnover)}。"
            f"理由：{board.reason}。"
        )
    lines.append("")
    return lines


def _render_candidate_section(config: AppConfig, analysis: MonthlyAnalysis) -> list[str]:
    lines = ["## 市场候选个股", ""]
    candidates = [item for item in analysis.candidates if item.score >= config.monthly.min_score]
    if not candidates:
        return lines + [f"- 暂无评分达到 {config.monthly.min_score:.0f} 的市场候选股。", ""]

    for item in candidates:
        lines.append(
            f"- {item.name}（{item.code}）：{_candidate_action(item.score)}，评分 {item.score:.1f}；"
            f"所属板块 {item.board}，现价 {item.price:.2f}，涨跌幅 {_pct(item.pct_change)}，"
            f"成交额 {_money(item.amount)}，换手率 {_pct(item.turnover)}，"
            f"营收同比 {_revenue_text(item.revenue_growth_pct, item.revenue_period)}。理由：{item.reason}。"
        )
    lines.append("")
    return lines


def _render_error_section(analysis: MonthlyAnalysis) -> list[str]:
    if not analysis.errors:
        return []
    lines = ["## 数据异常", ""]
    for error in analysis.errors[:10]:
        lines.append(f"- {error}")
    lines.append("")
    return lines


def _first_text(row: pd.Series, keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and not pd.isna(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _first_number(row: pd.Series, keys: list[str]) -> float:
    for key in keys:
        value = row.get(key)
        number = _to_float(value)
        if not isnan(number):
            return number
    return 0.0


def _fetch_revenue_growth(code: str) -> RevenueGrowth:
    loaders = [
        ("同花顺财务摘要", lambda: ak.stock_financial_abstract_ths(symbol=code)),
        ("同花顺新版财务摘要", lambda: ak.stock_financial_abstract_new_ths(symbol=code)),
        ("东方财富财务指标", lambda: ak.stock_financial_analysis_indicator_em(symbol=code)),
    ]
    errors: list[str] = []
    for source, loader in loaders:
        try:
            df = loader()
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            continue
        growth = _parse_revenue_growth(df, source)
        if growth.growth_pct is not None:
            return growth
        if growth.error:
            errors.append(f"{source}: {growth.error}")
    return RevenueGrowth(growth_pct=None, period="", source="", error=" | ".join(errors[-2:]) or "未找到营收同比字段")


def _parse_revenue_growth(df: pd.DataFrame, source: str) -> RevenueGrowth:
    if df is None or df.empty:
        return RevenueGrowth(growth_pct=None, period="", source=source, error="空数据")

    clean = df.copy()
    clean.columns = [str(col).strip() for col in clean.columns]
    rows = clean.fillna("").to_dict("records")

    row_growth = _parse_key_value_revenue_growth(rows)
    if row_growth.growth_pct is not None:
        return RevenueGrowth(growth_pct=row_growth.growth_pct, period=row_growth.period, source=source)

    for row in rows:
        period = _row_period(row)
        for key, value in row.items():
            key_text = str(key)
            if "营业" in key_text and "收入" in key_text and any(token in key_text for token in ("同比", "增长", "增速")):
                number = _to_float(value)
                if not isnan(number):
                    return RevenueGrowth(growth_pct=number, period=period, source=source)

    return RevenueGrowth(growth_pct=None, period="", source=source, error="未找到营收同比增长字段")


def _parse_key_value_revenue_growth(rows: list[dict[str, Any]]) -> RevenueGrowth:
    for row in rows:
        row_text = " ".join(str(value) for value in row.values())
        if "营业" not in row_text or "收入" not in row_text:
            continue
        if not any(token in row_text for token in ("同比", "增长", "增速")):
            continue
        numbers = [_to_float(value) for value in row.values()]
        numbers = [number for number in numbers if not isnan(number)]
        if numbers:
            return RevenueGrowth(growth_pct=numbers[-1], period=_row_period(row), source="")
    return RevenueGrowth(growth_pct=None, period="", source="", error=None)


def _row_period(row: dict[str, Any]) -> str:
    for key in ("报告期", "日期", "公告日期", "截止日期", "REPORT_DATE", "date"):
        value = str(row.get(key, "")).strip()
        if value:
            return value[:10]
    return "最近一期"


def _passes_revenue_check(revenue: RevenueGrowth, config: AppConfig) -> bool:
    if revenue.growth_pct is None:
        return config.monthly.allow_unknown_revenue
    return revenue.growth_pct >= config.monthly.min_revenue_growth_pct


def _to_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text in {"-", "--"}:
        return float("nan")
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return float("nan")


def _board_type_label(board_type: str) -> str:
    if board_type == "industry":
        return "行业"
    if board_type == "concept":
        return "概念"
    return board_type


def _index_name(code: str) -> str:
    names = {
        "000001": "上证指数",
        "399001": "深证成指",
        "000688": "科创50",
    }
    return names.get(code, code)


def _index_symbol(code: str) -> str:
    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


def _candidate_action(score: float) -> str:
    if score >= 85:
        return "重点跟踪"
    if score >= 75:
        return "积极观察"
    return "谨慎观察"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0 or pd.isna(denominator) or pd.isna(numerator):
        return 0.0
    return float(numerator) / float(denominator)


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def _money(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿"
    if value >= 10_000:
        return f"{value / 10_000:.2f} 万"
    return f"{value:.2f}"


def _revenue_text(value: float | None, period: str) -> str:
    if value is None:
        return "未确认"
    label = period or "最近一期"
    return f"{label} {value:.2f}%"
