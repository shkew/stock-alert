import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AppConfig, Position, Stock
from .data_sources import fetch_daily_history
from .event_monitor import collect_stock_events
from .indicators import enrich_indicators


@dataclass(frozen=True)
class RiskAlert:
    position: Position
    level: str
    category: str
    message: str
    fingerprint_key: str

    @property
    def fingerprint(self) -> str:
        raw = "|".join([self.position.code, self.level, self.category, self.fingerprint_key])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RiskMonitorResult:
    title: str
    markdown: str
    alerts: list[RiskAlert]
    errors: list[str]


def run_risk_monitor(config: AppConfig, update_state: bool = True) -> RiskMonitorResult:
    now = datetime.now()
    title = f"账户持仓风险提醒 {now:%Y-%m-%d %H:%M}"
    state = _load_state(config.risk.state_dir)
    seen = set(state.get("seen", []))
    alerts: list[RiskAlert] = []
    errors: list[str] = []

    for position in config.positions:
        position_alerts, position_errors = collect_position_risks(position, config)
        errors.extend([f"{position.name}（{position.code}）：{error}" for error in position_errors])
        alerts.extend([alert for alert in position_alerts if alert.fingerprint not in seen])

    if update_state:
        for alert in alerts:
            seen.add(alert.fingerprint)
        _save_state(config.risk.state_dir, {"updated_at": now.isoformat(), "seen": sorted(seen)[-5000:]})

    markdown = render_risk_result(title, config, alerts, errors)
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"risk_monitor_{now:%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return RiskMonitorResult(title=title, markdown=markdown, alerts=alerts, errors=errors)


def collect_position_risks(position: Position, config: AppConfig) -> tuple[list[RiskAlert], list[str]]:
    alerts: list[RiskAlert] = []
    errors: list[str] = []

    try:
        history = fetch_daily_history(position.code, config.risk.lookback_days)
        enriched = enrich_indicators(history)
        alerts.extend(_technical_risks(position, config, enriched))
    except Exception as exc:
        errors.append(f"行情/技术风险采集失败：{exc}")

    try:
        stock = Stock(code=position.code, name=position.name, sectors=[], themes=[])
        events, event_errors = collect_stock_events(stock, config)
        errors.extend(event_errors)
        for event in events[: config.monitor.max_events_per_stock]:
            keywords = f"；关键词：{', '.join(event.matched_keywords)}" if event.matched_keywords else ""
            alerts.append(
                RiskAlert(
                    position=position,
                    level="中",
                    category="事件风险",
                    message=f"{event.source}/{event.category}：{event.title}{keywords}",
                    fingerprint_key=event.fingerprint,
                )
            )
    except Exception as exc:
        errors.append(f"事件风险采集失败：{exc}")

    return sorted(alerts, key=_alert_rank), errors


def render_risk_result(title: str, config: AppConfig, alerts: list[RiskAlert], errors: list[str]) -> str:
    lines = [f"# {title}", "", f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    if not config.positions:
        lines.extend(["未配置账户持仓。请在 `config.yaml` 的 `positions` 中填写持仓股票。", ""])
    elif alerts:
        by_position: dict[str, list[RiskAlert]] = {}
        for alert in alerts:
            by_position.setdefault(f"{alert.position.name}（{alert.position.code}）", []).append(alert)
        for position_name, position_alerts in by_position.items():
            lines.extend([f"## {position_name}", ""])
            for alert in position_alerts:
                lines.append(f"- 【{alert.level}风险】{alert.category}：{alert.message}")
            lines.append("")
    else:
        lines.extend(["暂无新增账户持仓风险提示。", ""])

    if errors:
        lines.extend(["## 数据源异常", ""])
        lines.extend([f"- {error}" for error in errors])
        lines.append("")

    lines.extend(
        [
            "## 风控口径",
            "",
            f"- 成本预警：亏损达到 {abs(config.risk.loss_warn_pct):.1f}% 提醒，达到 {abs(config.risk.loss_danger_pct):.1f}% 升为高风险。",
            f"- 技术预警：单日跌幅达到 {abs(config.risk.single_day_drop_pct):.1f}%、跌破 MA20/MA60、放量下跌触发提示。",
            "- 事件预警：重大公告、财报、减持、处罚、监管、诉讼等关键词触发提示。",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _technical_risks(position: Position, config: AppConfig, enriched) -> list[RiskAlert]:
    if enriched.empty or len(enriched) < 60:
        return [
            RiskAlert(
                position=position,
                level="中",
                category="数据不足",
                message="历史行情不足，无法完整判断 MA20/MA60、量能和 RSI 风险。",
                fingerprint_key="insufficient-history",
            )
        ]

    latest = enriched.iloc[-1]
    previous = enriched.iloc[-2]
    close = float(latest["close"])
    pct_change = float(latest.get("pct_change", 0))
    ma20 = float(latest.get("ma20", 0))
    ma60 = float(latest.get("ma60", 0))
    volume_ratio = float(latest.get("volume_ratio", 0))
    rsi14 = float(latest.get("rsi14", 0))
    alerts: list[RiskAlert] = []

    if position.cost_price:
        pnl_pct = close / position.cost_price * 100 - 100
        market_value = close * position.quantity if position.quantity else None
        value_text = f"，当前市值约 {market_value:.2f}" if market_value is not None else ""
        if pnl_pct <= config.risk.loss_danger_pct:
            alerts.append(
                RiskAlert(position, "高", "成本风险", f"现价 {close:.2f}，较成本 {position.cost_price:.2f} 浮亏 {abs(pnl_pct):.2f}%{value_text}。", f"loss-danger-{latest['date'].date()}")
            )
        elif pnl_pct <= config.risk.loss_warn_pct:
            alerts.append(
                RiskAlert(position, "中", "成本风险", f"现价 {close:.2f}，较成本 {position.cost_price:.2f} 浮亏 {abs(pnl_pct):.2f}%{value_text}。", f"loss-warn-{latest['date'].date()}")
            )

    if position.stop_loss_price and close <= position.stop_loss_price:
        alerts.append(
            RiskAlert(position, "高", "止损风险", f"现价 {close:.2f} 已跌破止损价 {position.stop_loss_price:.2f}。", f"stop-loss-{latest['date'].date()}")
        )

    if pct_change <= config.risk.single_day_drop_pct:
        alerts.append(
            RiskAlert(position, "中", "单日波动", f"日跌幅 {pct_change:.2f}%，达到预警阈值。", f"daily-drop-{latest['date'].date()}")
        )

    if close < ma60:
        alerts.append(RiskAlert(position, "中", "趋势风险", f"收盘 {close:.2f} 低于 MA60 {ma60:.2f}，中期趋势偏弱。", f"below-ma60-{latest['date'].date()}"))
    elif close < ma20 and float(previous.get("close", close)) >= float(previous.get("ma20", ma20)):
        alerts.append(RiskAlert(position, "中", "趋势风险", f"收盘 {close:.2f} 跌破 MA20 {ma20:.2f}，短线转弱。", f"below-ma20-{latest['date'].date()}"))

    if pct_change < 0 and volume_ratio >= config.risk.volume_ratio_threshold:
        alerts.append(
            RiskAlert(position, "中", "量价风险", f"下跌同时成交量为 5 日均量的 {volume_ratio:.2f} 倍，疑似放量分歧。", f"down-volume-{latest['date'].date()}")
        )

    if 0 < rsi14 <= config.signals.rsi_oversold:
        alerts.append(RiskAlert(position, "低", "动能风险", f"RSI14 {rsi14:.1f}，处于偏冷区，需观察是否继续走弱。", f"rsi-low-{latest['date'].date()}"))
    return alerts


def _alert_rank(alert: RiskAlert) -> tuple[int, str]:
    level_rank = {"高": 0, "中": 1, "低": 2}
    return (level_rank.get(alert.level, 9), alert.category)


def _load_state(state_dir: Path) -> dict:
    path = state_dir / "risk_state.json"
    if not path.exists():
        return {"seen": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "risk_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
