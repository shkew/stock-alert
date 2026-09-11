import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import akshare as ak
import pandas as pd

from .config import AppConfig, Stock
from .data_sources import fetch_daily_history


@dataclass(frozen=True)
class VolumeAlert:
    stock: Stock
    level: str
    category: str
    price: float
    pct_change: float
    current_volume: float
    avg_volume: float
    projected_ratio: float
    message: str

    @property
    def fingerprint(self) -> str:
        today = datetime.now().strftime("%Y%m%d")
        raw = "|".join([today, self.stock.code, self.category, self.level])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VolumeMonitorResult:
    title: str
    markdown: str
    brief: str
    alerts: list[VolumeAlert]
    errors: list[str]


def run_volume_monitor(config: AppConfig, update_state: bool = True) -> VolumeMonitorResult:
    now = datetime.now()
    title = f"盘中量能提醒 {now:%Y-%m-%d %H:%M}"
    state = _load_state(config.volume_monitor.state_dir)
    seen = set(state.get("seen", []))
    alerts: list[VolumeAlert] = []
    errors: list[str] = []

    progress = _trading_progress(now)
    if progress < config.volume_monitor.min_progress:
        markdown = _render(title, [], [f"当前交易进度 {progress:.0%}，低于监控阈值，暂不判断量能。"])
        return VolumeMonitorResult(title, markdown, markdown, [], errors)

    if config.volume_monitor.source == "ths":
        ths_alerts, ths_errors = _collect_ths_rank_alerts(config)
        errors.extend(ths_errors)
        alerts.extend([alert for alert in ths_alerts if alert.fingerprint not in seen])
        if not alerts and not config.volume_monitor.eastmoney_fallback:
            errors = [] if not ths_errors else errors
    else:
        ths_alerts = []

    if config.volume_monitor.source != "ths" or (config.volume_monitor.eastmoney_fallback and not ths_alerts):
        spot, spot_error = _fetch_spot_map()
        if spot_error:
            errors.append(f"实时行情总表：{spot_error}；已尝试逐只股票分时兜底。")
        for stock in config.watchlist:
            try:
                stock_alerts = _collect_stock_volume_alerts(stock, config, spot, progress)
            except Exception as exc:
                errors.append(f"{stock.name}（{stock.code}）：{exc}")
                continue
            alerts.extend([alert for alert in stock_alerts if alert.fingerprint not in seen])

    alerts = sorted(alerts, key=_alert_rank)

    if update_state:
        for alert in alerts:
            seen.add(alert.fingerprint)
        _save_state(config.volume_monitor.state_dir, {"updated_at": now.isoformat(), "seen": sorted(seen)[-5000:]})

    brief = _render_brief(title, alerts, errors)
    markdown = _render(title, alerts, errors, brief)
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"volume_monitor_{now:%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return VolumeMonitorResult(title=title, markdown=markdown, brief=brief, alerts=alerts, errors=errors)


def _collect_stock_volume_alerts(
    stock: Stock,
    config: AppConfig,
    spot: dict[str, dict],
    progress: float,
) -> list[VolumeAlert]:
    row = spot.get(stock.code)
    history = fetch_daily_history(stock.code, config.volume_monitor.lookback_days)
    if history.empty:
        raise ValueError("历史成交量为空。")

    price, pct_change, current_volume = _current_snapshot(stock.code, row, history)
    if current_volume <= 0:
        raise ValueError("当前成交量为空。")

    avg_volume = float(history["volume"].tail(config.volume_monitor.avg_volume_days).mean())
    if avg_volume <= 0:
        raise ValueError("历史均量不可用。")

    projected_ratio = (current_volume / max(progress, 0.01)) / avg_volume
    alerts: list[VolumeAlert] = []
    abs_move = abs(pct_change)

    if projected_ratio >= config.volume_monitor.high_volume_ratio:
        if pct_change >= config.volume_monitor.price_move_pct:
            level, category = "高", "放量上涨"
            message = "盘中预计全天量显著高于近均量，且价格同步走强；关注是否突破关键均线或带动所属板块。"
        elif pct_change <= -config.volume_monitor.price_move_pct:
            level, category = "高", "放量下跌"
            message = "盘中放量但价格下跌，分歧或抛压偏强；持仓需优先看止损线和 MA20/MA60。"
        else:
            level, category = "中", "明显放量"
            message = "盘中量能明显放大，但价格方向尚未确认；等待涨跌幅和板块强度配合。"
        alerts.append(_alert(stock, level, category, price, pct_change, current_volume, avg_volume, projected_ratio, message))

    if projected_ratio <= config.volume_monitor.low_volume_ratio and abs_move >= config.volume_monitor.price_move_pct:
        direction = "上涨" if pct_change > 0 else "下跌"
        message = f"价格{direction}但预计全天量低于近均量，持续性需要打折；适合等补量确认。"
        alerts.append(_alert(stock, "中", f"缩量{direction}", price, pct_change, current_volume, avg_volume, projected_ratio, message))

    return alerts


def _collect_ths_rank_alerts(config: AppConfig) -> tuple[list[VolumeAlert], list[str]]:
    stock_map = {stock.code: stock for stock in config.watchlist}
    alerts: list[VolumeAlert] = []
    errors: list[str] = []
    rank_loaders = [
        ("高", "持续放量", "同花顺持续放量榜命中，说明资金关注度明显提高；需要结合价格方向和所属板块确认。", ak.stock_rank_cxfl_ths),
        ("中", "持续缩量", "同花顺持续缩量榜命中，说明交投趋冷；若上涨则注意持续性，若下跌则观察是否缩量企稳。", ak.stock_rank_cxsl_ths),
        ("高", "量价齐升", "同花顺量价齐升榜命中，量能和价格同步走强；关注是否带动板块并站上关键均线。", ak.stock_rank_ljqs_ths),
        ("高", "量价齐跌", "同花顺量价齐跌榜命中，放量下跌风险偏高；持仓优先看止损线和 MA20/MA60。", ak.stock_rank_ljqd_ths),
    ]

    for level, category, message, loader in rank_loaders:
        try:
            df = loader()
        except Exception as exc:
            errors.append(f"同花顺{category}榜采集失败：{exc}")
            continue
        if df is None or df.empty:
            continue

        for row in df.fillna("").to_dict("records"):
            code = _row_code(row)
            stock = stock_map.get(code)
            if not stock:
                continue
            price = _first_number(row, ["最新价", "现价", "收盘价", "价格"])
            pct_change = _first_number(row, ["涨跌幅", "涨幅"])
            current_volume = _first_number(row, ["成交量", "成交额"])
            ratio = _first_number(row, ["量比", "放量天数", "缩量天数"])
            if ratio <= 0:
                ratio = 1.0
            alerts.append(
                _alert(
                    stock=stock,
                    level=level,
                    category=category,
                    price=price,
                    pct_change=pct_change,
                    current_volume=current_volume,
                    avg_volume=0,
                    projected_ratio=ratio,
                    message=message,
                )
            )

    return _dedupe_alerts(alerts), errors


def _fetch_spot_map() -> tuple[dict[str, dict], str | None]:
    try:
        df = ak.stock_zh_a_spot_em()
    except Exception as exc:
        return {}, str(exc)
    if df is None or df.empty:
        return {}, "实时行情接口返回为空。"
    records = df.fillna("").to_dict("records")
    result: dict[str, dict] = {}
    for row in records:
        code = str(row.get("代码", "")).zfill(6)
        if code:
            result[code] = row
    return result, None


def _current_snapshot(code: str, row: dict | None, history: pd.DataFrame) -> tuple[float, float, float]:
    if row:
        price = _first_number(row, ["最新价", "价格", "收盘"])
        pct_change = _first_number(row, ["涨跌幅", "涨幅"])
        current_volume = _first_number(row, ["成交量"])
        if current_volume > 0:
            return price, pct_change, current_volume

    minute = ak.stock_zh_a_hist_pre_min_em(symbol=code, start_time="09:15:00", end_time="15:00:00")
    if minute is None or minute.empty:
        raise ValueError("分时行情为空。")

    clean = minute.rename(
        columns={
            "时间": "time",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "最新价": "latest",
        }
    ).copy()
    if "volume" not in clean.columns:
        volume_col = _find_column(clean, "成交量")
        if volume_col:
            clean = clean.rename(columns={volume_col: "volume"})
    close_col = "latest" if "latest" in clean.columns else "close"
    if close_col not in clean.columns:
        found = _find_column(clean, "收盘") or _find_column(clean, "最新价")
        if found:
            close_col = found
        else:
            raise ValueError("分时价格字段缺失。")
    if "volume" not in clean.columns:
        raise ValueError("分时成交量字段缺失。")

    volumes = pd.to_numeric(clean["volume"], errors="coerce").fillna(0)
    prices = pd.to_numeric(clean[close_col], errors="coerce").dropna()
    if prices.empty:
        raise ValueError("分时价格为空。")

    price = float(prices.iloc[-1])
    previous_close = _previous_close(history, price)
    pct_change = price / previous_close * 100 - 100 if previous_close else 0.0
    return price, pct_change, float(volumes.sum())


def _render_brief(title: str, alerts: list[VolumeAlert], errors: list[str]) -> str:
    lines = [title, f"生成：{datetime.now():%m-%d %H:%M}", ""]
    if not alerts:
        lines.append("结论：暂无新增盘中放量/缩量异动。")
    else:
        high = [alert for alert in alerts if alert.level == "高"]
        if high:
            lines.append(f"结论：出现 {len(high)} 条高优先级量价异动，优先看放量下跌风险和放量上涨延续。")
        else:
            lines.append("结论：有量能变化，但暂未达到高风险/高机会级别。")
        lines.extend(["", "异动："])
        for index, alert in enumerate(alerts[:5], start=1):
            lines.append(
                f"{index}. {alert.stock.name}：{alert.category}，涨跌{alert.pct_change:.2f}%，预计量比{alert.projected_ratio:.2f}。"
            )
            lines.append(f"   {alert.message}")

    if errors:
        lines.extend(["", f"数据源：{len(errors)} 个异常，完整记录见 reports。"])
    lines.append("提示：量能是触发信号，需要结合板块、均线和消息面确认。")
    return "\n".join(lines)


def _render(title: str, alerts: list[VolumeAlert], errors: list[str], brief: str | None = None) -> str:
    lines = [f"# {title}", "", f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    if brief:
        lines.extend(["## 推送简报", "", "```text", brief.strip(), "```", ""])

    if alerts:
        lines.extend(["## 量能异动", ""])
        for alert in alerts:
            if alert.avg_volume > 0:
                detail = (
                    f"现价 {alert.price:.2f}，涨跌幅 {alert.pct_change:.2f}%，"
                    f"当前成交量 {alert.current_volume:.0f}，近均量 {alert.avg_volume:.0f}，"
                    f"预计全天量比 {alert.projected_ratio:.2f}。"
                )
            else:
                detail = f"现价 {alert.price:.2f}，涨跌幅 {alert.pct_change:.2f}%。"
            lines.append(f"- 【{alert.level}】{alert.stock.name}（{alert.stock.code}）{alert.category}：{detail}{alert.message}")
        lines.append("")
    else:
        lines.extend(["暂无新增盘中量能异动。", ""])

    if errors:
        lines.extend(["## 数据源异常", ""])
        lines.extend([f"- {error}" for error in errors])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _trading_progress(now: datetime) -> float:
    current = now.time()
    sessions = [(time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))]
    total_minutes = 240
    elapsed = 0
    for start, end in sessions:
        if current >= end:
            elapsed += _minutes_between(start, end)
        elif current > start:
            elapsed += _minutes_between(start, current)
            break
    if current < sessions[0][0]:
        return 0.0
    if current > sessions[-1][1]:
        return 1.0
    return min(1.0, max(0.0, elapsed / total_minutes))


def _minutes_between(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _first_number(row: dict, keys: list[str]) -> float:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        text = str(value).replace("%", "").replace(",", "").strip()
        try:
            return float(text)
        except ValueError:
            continue
    return 0.0


def _row_code(row: dict) -> str:
    for key in ("代码", "股票代码", "证券代码", "code"):
        value = str(row.get(key, "")).strip()
        if value:
            return value.zfill(6)
    for key, value in row.items():
        key_text = str(key)
        if "代码" not in key_text and key_text.lower() != "code":
            continue
        text = str(value).strip()
        if text:
            return text.zfill(6)
    return ""


def _find_column(df: pd.DataFrame, keyword: str) -> str | None:
    for column in df.columns:
        if keyword in str(column):
            return str(column)
    return None


def _previous_close(history: pd.DataFrame, current_price: float) -> float:
    if history.empty:
        return current_price
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if closes.empty:
        return current_price
    if len(closes) >= 2 and abs(float(closes.iloc[-1]) - current_price) / max(current_price, 0.01) < 0.03:
        return float(closes.iloc[-2])
    return float(closes.iloc[-1])


def _alert(
    stock: Stock,
    level: str,
    category: str,
    price: float,
    pct_change: float,
    current_volume: float,
    avg_volume: float,
    projected_ratio: float,
    message: str,
) -> VolumeAlert:
    return VolumeAlert(stock, level, category, price, pct_change, current_volume, avg_volume, projected_ratio, message)


def _alert_rank(alert: VolumeAlert) -> tuple[int, float]:
    level_rank = {"高": 0, "中": 1, "低": 2}
    return (level_rank.get(alert.level, 9), -alert.projected_ratio)


def _dedupe_alerts(alerts: list[VolumeAlert]) -> list[VolumeAlert]:
    seen: set[str] = set()
    result: list[VolumeAlert] = []
    for alert in alerts:
        key = f"{alert.stock.code}-{alert.category}"
        if key in seen:
            continue
        seen.add(key)
        result.append(alert)
    return result


def _load_state(state_dir: Path) -> dict:
    path = state_dir / "volume_state.json"
    if not path.exists():
        return {"seen": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "volume_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
