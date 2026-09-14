import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak

from .config import AppConfig, Stock
from .data_sources import fetch_stock_news
from .monthly_analysis import _fetch_market_indexes, _market_environment
from .overseas_source import fetch_yahoo_history, fetch_yahoo_news


@dataclass(frozen=True)
class Event:
    stock: Stock
    source: str
    category: str
    title: str
    date: str
    url: str
    matched_keywords: list[str]

    @property
    def fingerprint(self) -> str:
        raw = "|".join([self.stock.code, self.source, self.category, self.title, self.date, self.url])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MonitorResult:
    title: str
    markdown: str
    brief: str
    new_events: list[Event]
    errors: list[str]


def run_event_monitor(config: AppConfig, update_state: bool = True, ignore_state: bool = False) -> MonitorResult:
    now = datetime.now()
    title = f"股票重大事件提醒 {now:%Y-%m-%d %H:%M}"
    state = _load_state(config.monitor.state_dir)
    seen = set(state.get("seen", []))
    all_new: list[Event] = []
    errors: list[str] = []

    for stock in config.watchlist:
        events, stock_errors = collect_stock_events(stock, config)
        errors.extend([f"{stock.name}（{stock.code}）：{error}" for error in stock_errors])

        fresh = events if ignore_state else [event for event in events if event.fingerprint not in seen]
        all_new.extend(fresh[: config.monitor.max_events_per_stock])

    if config.overseas.enabled:
        for asset in [*config.overseas.indexes, *config.overseas.watchlist]:
            stock = Stock(code=asset.symbol, name=asset.name, sectors=[asset.market], themes=asset.themes)
            try:
                events = _overseas_news_events(stock, config)
            except Exception as exc:
                errors.append(f"{asset.name}（{asset.symbol}）：外围新闻采集失败：{exc}")
                continue
            fresh = events if ignore_state else [event for event in events if event.fingerprint not in seen]
            all_new.extend(fresh[: config.monitor.max_events_per_stock])

    if update_state and not ignore_state:
        for event in all_new:
            seen.add(event.fingerprint)
        _save_state(config.monitor.state_dir, {"updated_at": now.isoformat(), "seen": sorted(seen)[-5000:]})

    ranked_events = sorted(all_new, key=_event_rank)
    brief = render_monitor_brief(title, ranked_events, errors, config)
    markdown = render_monitor_result(title, ranked_events, errors, brief)
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"event_monitor_{now:%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return MonitorResult(title=title, markdown=markdown, brief=brief, new_events=ranked_events, errors=errors)


def run_morning_brief(config: AppConfig) -> MonitorResult:
    now = datetime.now()
    title = f"A股早盘简报 {now:%Y-%m-%d}"
    events: list[Event] = []
    errors: list[str] = []

    for stock in config.watchlist:
        stock_events, stock_errors = collect_stock_events(stock, config)
        events.extend(stock_events[: config.monitor.max_events_per_stock])
        errors.extend([f"{stock.name}（{stock.code}）：{error}" for error in stock_errors])

    if config.overseas.enabled:
        for asset in [*config.overseas.indexes, *config.overseas.watchlist]:
            stock = Stock(code=asset.symbol, name=asset.name, sectors=[asset.market], themes=asset.themes)
            try:
                events.extend(_overseas_news_events(stock, config)[:2])
            except Exception as exc:
                errors.append(f"{asset.name}（{asset.symbol}）：外围新闻采集失败：{exc}")

    ranked_events = sorted(_dedupe_events(events), key=_event_rank)
    brief = render_morning_brief(title, ranked_events, errors, config)
    markdown = render_monitor_result(title, ranked_events, errors, brief)
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"morning_brief_{now:%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return MonitorResult(title=title, markdown=markdown, brief=brief, new_events=ranked_events, errors=errors)


def collect_stock_events(stock: Stock, config: AppConfig) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []

    for source_name, loader in (
        ("新闻", _news_events),
        ("公告", _notice_events),
        ("财报披露", _disclosure_events),
    ):
        try:
            events.extend(loader(stock, config))
        except Exception as exc:
            errors.append(f"{source_name}采集失败：{exc}")

    return sorted(events, key=lambda item: item.date, reverse=True), errors


def render_monitor_result(title: str, events: list[Event], errors: list[str], brief: str | None = None) -> str:
    lines = [f"# {title}", "", f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    if brief:
        lines.extend(["## 推送简报", "", "```text", brief.strip(), "```", ""])

    if events:
        lines.extend(["## 原始事件", ""])
        by_stock: dict[str, list[Event]] = {}
        for event in events:
            by_stock.setdefault(f"{event.stock.name}（{event.stock.code}）", []).append(event)

        for stock_name, stock_events in by_stock.items():
            lines.extend([f"## {stock_name}", ""])
            for event in stock_events:
                keywords = f"；关键词：{', '.join(event.matched_keywords)}" if event.matched_keywords else ""
                prefix = " / ".join(part for part in [event.source, event.category, event.date] if part)
                if event.url:
                    lines.append(f"- {prefix} [{event.title}]({event.url}){keywords}")
                else:
                    lines.append(f"- {prefix} {event.title}{keywords}")
            lines.append("")
    else:
        lines.extend(["暂无新增重大消息、公告或财报事件。", ""])

    if errors:
        lines.extend(["## 数据源异常", ""])
        lines.extend([f"- {error}" for error in errors])
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_monitor_brief(title: str, events: list[Event], errors: list[str], config: AppConfig | None = None) -> str:
    lines = [title, f"生成：{datetime.now():%m-%d %H:%M}", ""]

    if not events:
        lines.extend(
            [
                "结论：暂无新增高权重事件。",
                "关注：继续等待公告、财报、业绩预告或外围科技链变化。",
            ]
        )
        if errors:
            lines.append(f"数据源：{len(errors)} 个接口异常，完整记录见 reports。")
        return "\n".join(lines)

    themes = _theme_counts(events)
    conclusion = _brief_conclusion(events, themes)
    lines.extend(["结论：", conclusion, ""])

    lines.append("重点：")
    for index, event in enumerate(events[:6], start=1):
        impact = _event_impact(event)
        lines.append(f"{index}. {event.stock.name}：{_short_title(event.title)}")
        lines.append(f"   {impact}")

    lines.extend(["", "趋势联动："])
    lines.extend(_trend_lines(events, themes))

    if config is not None:
        lines.extend(["", "A股趋势和机会："])
        lines.extend(_a_share_opportunity_lines(config, events, themes))

    if errors:
        lines.extend(["", f"数据源：{len(errors)} 个接口异常，完整记录见 reports。"])
    lines.append("完整原文和链接已保存到 reports。")
    return "\n".join(lines)


def render_morning_brief(title: str, events: list[Event], errors: list[str], config: AppConfig) -> str:
    lines = [title, f"生成：{datetime.now():%m-%d %H:%M}", ""]
    themes = _theme_counts(events)

    lines.append(f"外围：{_overseas_market_move_line()}；{_overseas_one_liner(events, themes)}")
    lines.append(f"A股新闻：{_a_share_news_one_liner(events)}")
    lines.append("")
    lines.append("A股潜力方向：")
    lines.extend(_morning_a_share_lines(config, events, themes))

    if errors:
        lines.append(f"数据提示：{len(errors)} 个接口异常，详见 reports。")
    lines.append("仅作信息跟踪，不构成买卖建议。")
    return "\n".join(lines)


def _news_events(stock: Stock, config: AppConfig) -> list[Event]:
    events = []
    for item in fetch_stock_news(stock.code, config.report.news_limit * 3):
        title = item.get("title", "")
        matched = _matched_keywords(title, config.monitor.important_keywords)
        if not matched:
            continue
        events.append(
            Event(
                stock=stock,
                source="东方财富新闻",
                category="消息面",
                title=title,
                date=item.get("date", ""),
                url=item.get("url", ""),
                matched_keywords=matched,
            )
        )
    return events


def _overseas_news_events(stock: Stock, config: AppConfig) -> list[Event]:
    events = []
    for item in fetch_yahoo_news(stock.code, config.overseas.news_limit * 3):
        title = item.get("title", "")
        matched = _matched_keywords(title, config.monitor.important_keywords)
        if not matched or not _is_relevant_overseas_news(stock, title, matched):
            continue
        events.append(
            Event(
                stock=stock,
                source="Yahoo Finance",
                category="外围消息",
                title=title,
                date=item.get("date", ""),
                url=item.get("url", ""),
                matched_keywords=matched,
            )
        )
    return events


def _notice_events(stock: Stock, config: AppConfig) -> list[Event]:
    begin = (datetime.now() - timedelta(days=config.monitor.lookback_days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    categories = ["重大事项", "财务报告", "风险提示", "信息变更", "持股变动", "全部"]
    events: list[Event] = []
    errors: list[str] = []

    for category in categories:
        try:
            df = ak.stock_individual_notice_report(
                security=stock.code,
                symbol=category,
                begin_date=begin,
                end_date=end,
            )
        except Exception as exc:
            errors.append(f"{category}: {exc}")
            continue

        for row in _rows(df):
            title = _first(row, ["公告标题", "标题", "notice_title", "title"])
            if not title:
                continue
            matched = _matched_keywords(title, config.monitor.important_keywords)
            if category != "全部" or matched:
                events.append(
                    Event(
                        stock=stock,
                        source="东方财富公告",
                        category=category,
                        title=title,
                        date=_first(row, ["公告日期", "公告时间", "日期", "date"]),
                        url=_first(row, ["公告链接", "链接", "url"]),
                        matched_keywords=matched,
                    )
                )

    if not events and errors:
        raise RuntimeError("公告接口失败；" + " | ".join(errors[-3:]))
    return _dedupe_events(events)


def _disclosure_events(stock: Stock, config: AppConfig) -> list[Event]:
    start = (datetime.now() - timedelta(days=config.monitor.lookback_days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    events: list[Event] = []
    errors: list[str] = []

    for keyword in ("年报", "中报", "半年报", "季报", "业绩预告", "业绩快报"):
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=stock.code,
                market="沪深京",
                keyword=keyword,
                category="",
                start_date=start,
                end_date=end,
            )
        except Exception as exc:
            errors.append(f"{keyword}: {exc}")
            continue

        for row in _rows(df):
            title = _first(row, ["公告标题", "标题", "secName", "announcementTitle", "title"])
            if not title:
                continue
            matched = _matched_keywords(title, config.monitor.important_keywords)
            events.append(
                Event(
                    stock=stock,
                    source="巨潮披露",
                    category=keyword,
                    title=title,
                    date=_first(row, ["公告时间", "公告日期", "publishTime", "date"]),
                    url=_first(row, ["公告链接", "链接", "adjunctUrl", "url"]),
                    matched_keywords=matched,
                )
            )

    if not events and errors:
        raise RuntimeError("巨潮披露接口失败；" + " | ".join(errors[-3:]))
    return _dedupe_events(events)


def _load_state(state_dir: Path) -> dict:
    path = state_dir / "event_state.json"
    if not path.exists():
        return {"seen": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "event_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _rows(df) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.fillna("").to_dict("records")


def _first(row: dict, names: list[str]) -> str:
    for name in names:
        value = str(row.get(name, "")).strip()
        if value:
            return " ".join(value.split())
    for key, value in row.items():
        if any(token in str(key) for token in ("标题", "日期", "时间", "链接")):
            text = str(value).strip()
            if text:
                return " ".join(text.split())
    return ""


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword and keyword.lower() in lowered]


def _dedupe_events(events: list[Event]) -> list[Event]:
    seen: set[str] = set()
    result: list[Event] = []
    for event in events:
        key = event.fingerprint
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def _event_rank(event: Event) -> tuple[int, str]:
    text = f"{event.category} {event.title}".lower()
    score = 0
    priority_terms = {
        "重大": 25,
        "业绩预告": 25,
        "业绩快报": 25,
        "年报": 22,
        "中报": 22,
        "半年报": 22,
        "季报": 18,
        "重组": 24,
        "并购": 20,
        "收购": 20,
        "回购": 18,
        "增持": 16,
        "减持": 20,
        "处罚": 20,
        "监管": 18,
        "中标": 14,
        "合同": 14,
        "earnings": 18,
        "guidance": 18,
        "revenue": 14,
        "semiconductor": 20,
        "chip": 16,
        "ai": 14,
        "tariff": 16,
        "korea": 12,
    }
    for term, value in priority_terms.items():
        if term.lower() in text:
            score += value
    if event.source in {"东方财富公告", "巨潮披露"}:
        score += 15
    if event.category in {"财务报告", "重大事项", "风险提示"}:
        score += 12
    return (-score, event.date)


def _theme_counts(events: list[Event]) -> dict[str, int]:
    counts = {"AI/半导体": 0, "财报业绩": 0, "韩国链": 0, "股东回报": 0, "监管风险": 0, "A股公告": 0}
    for event in events:
        text = f"{event.stock.name} {event.stock.code} {event.category} {event.title} {' '.join(event.matched_keywords)}".lower()
        if any(term in text for term in ("ai", "chip", "semiconductor", "nvidia", "broadcom", "micron", "hbm", "半导体", "芯片", "算力")):
            counts["AI/半导体"] += 1
        if any(term in text for term in ("earnings", "guidance", "revenue", "profit", "业绩", "年报", "中报", "季报", "快报", "预告")):
            counts["财报业绩"] += 1
        if any(term in text for term in ("korea", ".ks", "kospi", "kosdaq", "samsung", "sk hynix", "韩国", "三星", "海力士")):
            counts["韩国链"] += 1
        if any(term in text for term in ("dividend", "buyback", "分红", "派息", "回购")):
            counts["股东回报"] += 1
        if any(term in text for term in ("tariff", "处罚", "监管", "诉讼", "风险")):
            counts["监管风险"] += 1
        if event.source in {"东方财富公告", "巨潮披露", "东方财富新闻"} and not event.stock.code.startswith("^"):
            counts["A股公告"] += 1
    return counts


def _brief_conclusion(events: list[Event], themes: dict[str, int]) -> str:
    leaders = [name for name, count in sorted(themes.items(), key=lambda item: item[1], reverse=True) if count > 0][:3]
    if not leaders:
        return "新增事件偏分散，暂未形成清晰主线。"
    if themes.get("AI/半导体", 0) >= 2:
        return "外围科技链消息最密集，AI/半导体仍是主要观察方向；若 A 股相关板块放量走强，可提高跟踪优先级。"
    if themes.get("财报业绩", 0) >= 2:
        return "新增事件集中在财报和业绩预期，短线更适合看业绩兑现与估值反应。"
    return f"新增事件集中在{'、'.join(leaders)}，先观察是否能带动板块成交和趋势延续。"


def _event_impact(event: Event) -> str:
    text = f"{event.title} {' '.join(event.matched_keywords)}".lower()
    if any(term in text for term in ("ai", "chip", "semiconductor", "hbm", "nvidia", "broadcom", "micron")):
        return "影响：偏科技成长线，关注 A 股半导体、算力、存储、消费电子的情绪传导。"
    if any(term in text for term in ("earnings", "guidance", "revenue", "profit", "业绩", "快报", "预告", "年报", "中报")):
        return "影响：偏业绩线，重点看预期差、营收质量和公告后股价是否放量确认。"
    if any(term in text for term in ("dividend", "buyback", "分红", "回购", "增持")):
        return "影响：偏股东回报和资金偏好，关注高股息、低估值方向。"
    if any(term in text for term in ("tariff", "处罚", "监管", "诉讼", "减持")):
        return "影响：偏风险项，相关持仓先看仓位和止损线。"
    return f"影响：与{event.category}相关，先观察是否引发板块或个股趋势变化。"


def _trend_lines(events: list[Event], themes: dict[str, int]) -> list[str]:
    lines: list[str] = []
    if themes.get("AI/半导体", 0):
        lines.append("- AI/半导体：消息热度靠前，需结合 SOX、NVDA、韩国存储链趋势判断持续性。")
    if themes.get("韩国链", 0):
        lines.append("- 韩国链：三星、SK海力士、KOSPI/KOSDAQ 对存储、消费电子、电池链有参考意义。")
    if themes.get("财报业绩", 0):
        lines.append("- 财报线：中报/年报/业绩预告只算触发点，放量突破或跌破均线才算趋势确认。")
    if themes.get("A股公告", 0):
        lines.append("- A股自选：优先看公告是否改变盈利预期，再看 MA20/MA60 和成交量配合。")
    if not lines:
        lines.append("- 暂无明确趋势主线，保持观察。")
    return lines[:4]


def _a_share_opportunity_lines(config: AppConfig, events: list[Event], themes: dict[str, int]) -> list[str]:
    hints = _message_board_hints(events, themes)
    errors: list[str] = []
    try:
        indexes = _fetch_market_indexes(config, errors)
        market_score, market_state = _market_environment(indexes)
        boards = _fetch_quick_ths_industry_boards()
    except Exception as exc:
        if hints:
            return [f"- {hint}：消息触发，板块扫描暂不可用，先观察放量和 MA20/MA60 趋势确认。" for hint in hints[:4]] + [
                f"- 板块扫描异常：{exc}"
            ]
        return [f"- 板块扫描暂不可用：{exc}"]

    lines: list[str] = []
    if indexes:
        strongest = max(indexes, key=lambda item: item.score)
        weakest = min(indexes, key=lambda item: item.score)
        lines.append(
            f"- 大盘：{market_state}，环境分 {market_score:.0f}；"
            f"最强 {strongest.name} {strongest.state}，最弱 {weakest.name} {weakest.state}。"
        )
    else:
        lines.append(f"- 大盘：{market_state}，环境分 {market_score:.0f}。")

    matched_boards = []
    for board in boards:
        if hints and not any(hint in board.name or board.name in hint for hint in hints):
            continue
        matched_boards.append(board)

    selected_boards = matched_boards[:3] if matched_boards else boards[:4]
    if selected_boards:
        board_text = "；".join(
            f"{board.name}{_board_bias(board.score)} 分{board.score:.0f} 涨{board.pct_change:.2f}%"
            for board in selected_boards
        )
        lines.append(f"- 板块趋势：{board_text}。")

    for board in selected_boards:
        if board.leader_name:
            lines.append(
                f"- {board.name}：{board.reason}；领涨观察："
                f"{_leader_label(board)} 涨{board.leader_pct_change:.2f}% 现价{board.leader_price:.2f}。"
            )
        else:
            lines.append(f"- {board.name}：{board.reason}；暂无领涨股字段。")

    leaders = [board for board in boards if board.leader_name][:5]
    if leaders:
        leader_text = "；".join(
            f"{_leader_label(board)} {board.name} 涨{board.leader_pct_change:.2f}%"
            for board in leaders
        )
        lines.append(f"- 个股观察池：{leader_text}。")

    if len(lines) <= 1 and hints:
        lines.extend([f"- {hint}：消息热度出现，等待板块涨幅、成交额和个股趋势确认。" for hint in hints[:4]])
    if len(lines) <= 1:
        lines.append("- 暂无明确板块机会，等待消息和趋势共振。")

    if errors:
        lines.append(f"- 数据提示：{len(errors)} 个大盘接口异常，完整记录见 reports。")
    return lines[:9]


def _morning_a_share_lines(config: AppConfig, events: list[Event], themes: dict[str, int]) -> list[str]:
    errors: list[str] = []
    try:
        indexes = _fetch_market_indexes(config, errors)
        market_score, market_state = _market_environment(indexes)
        boards = _fetch_quick_ths_industry_boards()
    except Exception as exc:
        hints = _message_board_hints(events, themes)
        if hints:
            return [f"- 主线：{', '.join(hints[:3])}；板块接口暂不可用，先等放量和均线确认。", f"- 异常：{exc}"]
        return [f"- 板块接口暂不可用：{exc}"]

    lines: list[str] = []
    if indexes:
        strongest = max(indexes, key=lambda item: item.score)
        lines.append(f"- 大盘：{market_state}，分{market_score:.0f}；相对强的是{strongest.name}。")
    else:
        inferred_score, inferred_state = _infer_market_from_boards(boards)
        lines.append(f"- 大盘：指数接口暂不可用，按板块热度推断为{inferred_state}，分{inferred_score:.0f}。")

    top_boards = boards[:3]
    if top_boards:
        board_text = "；".join(f"{board.name} 涨{board.pct_change:.2f}% 分{board.score:.0f}" for board in top_boards)
        lines.append(f"- 板块：{board_text}。")
    else:
        lines.append("- 板块：暂无明显强势板块。")

    picks = _three_cross_board_picks(boards)
    if picks:
        lines.append("- 观察股：")
        for board in picks:
            lines.append(f"  {_leader_label(board)}：{board.name}，涨{board.leader_pct_change:.2f}%，现价{board.leader_price:.2f}。")
    else:
        lines.append("- 观察股：暂无可用领涨股。")

    hints = _message_board_hints(events, themes)
    if hints:
        lines.append(f"- 新闻主线：{', '.join(hints[:3])}。")
    if errors:
        lines.append(f"- 数据：{len(errors)} 个指数接口异常。")
    return lines[:7]


def _three_cross_board_picks(boards: list["QuickBoard"]) -> list["QuickBoard"]:
    picks: list[QuickBoard] = []
    used_boards: set[str] = set()
    used_names: set[str] = set()
    for board in boards:
        if not board.leader_name or board.name in used_boards or board.leader_name in used_names:
            continue
        picks.append(board)
        used_boards.add(board.name)
        used_names.add(board.leader_name)
        if len(picks) >= 3:
            break
    return picks


def _leader_label(board: "QuickBoard") -> str:
    if board.leader_code:
        return f"{board.leader_name}({board.leader_code})"
    return f"{board.leader_name}(代码待确认)"


def _overseas_one_liner(events: list[Event], themes: dict[str, int]) -> str:
    overseas_events = [event for event in events if event.category == "外围消息"]
    if not overseas_events:
        return "暂无高权重外围扰动。"
    parts = []
    if themes.get("AI/半导体", 0):
        parts.append(f"科技链{themes['AI/半导体']}条")
    if themes.get("韩国链", 0):
        parts.append(f"韩国链{themes['韩国链']}条")
    if themes.get("监管风险", 0):
        parts.append(f"利率/风险{themes['监管风险']}条")
    label = "、".join(parts) if parts else f"{len(overseas_events)}条"
    return f"{label}，只作为A股情绪参考。"


def _overseas_market_move_line() -> str:
    assets = [
        ("黄金", ["GC=F", "GLD"]),
        ("纳指", ["^IXIC", "QQQ"]),
        ("费半", ["^SOX", "SOXX"]),
    ]
    parts: list[str] = []
    for name, symbols in assets:
        pct_change = _first_overseas_pct(symbols)
        parts.append(f"{name}{_signed_pct(pct_change)}" if pct_change is not None else f"{name}暂无")
    return "、".join(parts)


def _first_overseas_pct(symbols: list[str]) -> float | None:
    for symbol in symbols:
        try:
            history = fetch_yahoo_history(symbol, 10)
            latest = history.iloc[-1]
            return float(latest.get("pct_change", 0))
        except Exception:
            continue
    return None


def _signed_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _a_share_news_one_liner(events: list[Event]) -> str:
    a_events = [
        event
        for event in events
        if event.category != "外围消息" and not event.stock.code.startswith("^") and _is_recent_event(event, days=3)
    ]
    if not a_events:
        return "暂无近3日自选股高权重新闻，重点看板块资金。"
    items = [f"{event.stock.name}:{_short_title(event.title, 24)}" for event in a_events[:2]]
    return "；".join(items)


def _is_recent_event(event: Event, days: int) -> bool:
    parsed = _parse_event_datetime(event.date)
    if parsed is None:
        return False
    return parsed >= datetime.now() - timedelta(days=days)


def _parse_event_datetime(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


@dataclass(frozen=True)
class QuickBoard:
    name: str
    score: float
    pct_change: float
    amount: float
    leader_name: str
    leader_code: str
    leader_price: float
    leader_pct_change: float
    reason: str


def _fetch_quick_ths_industry_boards() -> list[QuickBoard]:
    try:
        df = ak.stock_board_industry_summary_ths()
        source = "ths"
    except Exception:
        df = ak.stock_board_industry_name_em()
        source = "em"
    code_map = _stock_code_name_map()
    boards: list[QuickBoard] = []
    for row in _rows(df):
        name = _first(row, ["板块", "板块名称", "名称"])
        if not name:
            continue
        pct_change = _number(_first(row, ["涨跌幅", "涨跌幅%", "涨幅"]))
        amount = _quick_board_amount(row, source)
        leader_name = _first(row, ["领涨股", "领涨股票"])
        leader_code = code_map.get(leader_name, "")
        leader_price = _number(_first(row, ["领涨股-最新价", "领涨股最新价"]))
        leader_pct_change = _number(_first(row, ["领涨股-涨跌幅", "领涨股涨跌幅"]))
        if not leader_name or not leader_code or leader_price <= 0:
            leader_name, leader_code, leader_price, leader_pct_change = _fill_board_leader(
                name,
                leader_name,
                leader_code,
                leader_price,
                leader_pct_change,
                code_map,
            )
        score = _quick_board_score(pct_change, amount)
        boards.append(
            QuickBoard(
                name=name,
                score=score,
                pct_change=pct_change,
                amount=amount,
                leader_name=leader_name,
                leader_code=leader_code,
                leader_price=leader_price,
                leader_pct_change=leader_pct_change,
                reason=_quick_board_reason(pct_change, amount),
            )
        )
    return sorted([board for board in boards if board.pct_change > 0], key=lambda item: item.score, reverse=True)[:10]


def _quick_board_amount(row: dict, source: str) -> float:
    amount = _number(_first(row, ["成交额", "总成交额", "成交金额"]))
    if source == "ths" and amount and amount < 100_000_000:
        return amount * 100_000_000
    return amount


def _fill_board_leader(
    board_name: str,
    leader_name: str,
    leader_code: str,
    leader_price: float,
    leader_pct_change: float,
    code_map: dict[str, str],
) -> tuple[str, str, float, float]:
    if leader_name and not leader_code:
        leader_code = code_map.get(leader_name, "")
    if leader_name and leader_code and leader_price > 0:
        return leader_name, leader_code, leader_price, leader_pct_change

    try:
        df = ak.stock_board_industry_cons_em(symbol=board_name)
    except Exception:
        return leader_name, leader_code, leader_price, leader_pct_change

    best: tuple[str, str, float, float, float] | None = None
    for row in _rows(df):
        name = _first(row, ["名称", "股票简称", "股票名称"])
        code = _first(row, ["代码", "股票代码", "证券代码"])
        pct_change = _number(_first(row, ["涨跌幅", "涨幅", "涨跌幅%"]))
        price = _number(_first(row, ["最新价", "收盘", "价格"]))
        amount = _number(_first(row, ["成交额", "成交金额"]))
        if not name:
            continue
        score = pct_change * 10 + min(amount / 100_000_000, 30)
        if best is None or score > best[4]:
            best = (name, code.zfill(6) if code else code_map.get(name, ""), price, pct_change, score)

    if best is None:
        return leader_name, leader_code, leader_price, leader_pct_change
    return best[0], best[1], best[2], best[3]


def _quick_board_score(pct_change: float, amount: float) -> float:
    score = 50
    score += max(-15, min(25, pct_change * 5))
    score += max(0, min(18, amount / 10_000_000_000 * 12))
    return round(max(0, min(100, score)), 1)


def _quick_board_reason(pct_change: float, amount: float) -> str:
    reasons = []
    if pct_change >= 3:
        reasons.append("板块涨幅靠前")
    elif pct_change > 0:
        reasons.append("板块上涨")
    if amount >= 20_000_000_000:
        reasons.append("成交额活跃")
    return "；".join(reasons) if reasons else "热度一般"


def _infer_market_from_boards(boards: list[QuickBoard]) -> tuple[float, str]:
    if not boards:
        return 50.0, "中性"
    top = boards[:5]
    avg_score = sum(board.score for board in top) / len(top)
    avg_change = sum(board.pct_change for board in top) / len(top)
    score = round(max(0, min(100, avg_score - 12)), 1)
    if avg_change >= 2.5 and avg_score >= 75:
        return score, "局部偏强"
    if avg_change >= 1:
        return score, "结构性活跃"
    if avg_change > 0:
        return score, "弱修复"
    return score, "偏弱"


def _board_bias(score: float) -> str:
    if score >= 75:
        return "偏强"
    if score >= 60:
        return "可观察"
    return "一般"


def _candidate_brief(item) -> str:
    return (
        f"{item.name}({item.code}) 评分{item.score:.0f} "
        f"{item.board} 涨{item.pct_change:.2f}% 成交{_brief_money(item.amount)}"
    )


def _brief_money(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.0f}万"
    return f"{value:.0f}"


def _number(value) -> float:
    if value is None:
        return 0.0
    text = str(value).replace("%", "").replace(",", "").strip()
    if not text or text in {"-", "--", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _stock_code_name_map() -> dict[str, str]:
    try:
        df = ak.stock_info_a_code_name()
    except Exception:
        return {}
    result: dict[str, str] = {}
    for row in _rows(df):
        code = _first(row, ["code", "证券代码", "股票代码", "代码"])
        name = _first(row, ["name", "证券简称", "股票简称", "名称"])
        if code and name:
            result[name] = code.zfill(6)
    return result


def _message_board_hints(events: list[Event], themes: dict[str, int]) -> list[str]:
    hints: list[str] = []
    text = " ".join(f"{event.stock.name} {event.title} {' '.join(event.matched_keywords)} {' '.join(event.stock.themes)}" for event in events).lower()
    mapping = [
        (("ai", "算力", "gpu"), "算力"),
        (("semiconductor", "chip", "hbm", "半导体", "芯片", "存储"), "半导体"),
        (("apple", "iphone", "消费电子"), "消费电子"),
        (("tesla", "ev", "新能源车", "robotaxi"), "新能源汽车"),
        (("battery", "电池", "动力电池"), "电池"),
        (("dividend", "分红", "派息", "高股息"), "高股息"),
        (("bank", "银行", "利率", "rate"), "银行"),
        (("cips", "跨境支付", "金融科技"), "金融科技"),
        (("白酒", "消费", "食品饮料"), "食品饮料"),
        (("机器人",), "机器人"),
    ]
    for tokens, board in mapping:
        if any(token in text for token in tokens) and board not in hints:
            hints.append(board)
    if themes.get("AI/半导体", 0) and "半导体" not in hints:
        hints.append("半导体")
    if themes.get("韩国链", 0) and "存储芯片" not in hints:
        hints.append("存储芯片")
    return hints


def _short_title(title: str, limit: int = 72) -> str:
    clean = " ".join(title.replace("[", "").replace("]", "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _is_relevant_overseas_news(stock: Stock, title: str, matched: list[str]) -> bool:
    text = title.lower()
    if stock.code.startswith("^"):
        return bool(matched)

    identity_terms = {stock.code.lower().replace(".ks", ""), stock.name.lower()}
    alias_map = {
        "NVDA": {"nvidia", "nvda", "ai", "gpu", "semiconductor", "chip", "foxconn", "tsmc", "broadcom", "micron"},
        "AAPL": {"apple", "aapl", "iphone", "consumer electronics", "foxconn"},
        "TSLA": {"tesla", "tsla", "ev", "robotaxi", "battery"},
        "005930.KS": {"samsung", "memory", "hbm", "semiconductor", "chip"},
        "000660.KS": {"sk hynix", "hynix", "memory", "hbm", "semiconductor", "chip"},
        "373220.KS": {"lg energy", "battery", "ev"},
    }
    identity_terms.update(alias_map.get(stock.code, set()))
    for theme in stock.themes:
        identity_terms.add(theme.lower())

    if any(term and term in text for term in identity_terms):
        return True
    high_signal = {"earnings", "guidance", "revenue", "tariff", "semiconductor", "chip"}
    return bool(high_signal.intersection({item.lower() for item in matched}))
