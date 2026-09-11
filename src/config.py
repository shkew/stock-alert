from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    sectors: list[str]
    themes: list[str]


@dataclass(frozen=True)
class ReportConfig:
    title: str
    history_days: int
    news_limit: int
    output_dir: Path


@dataclass(frozen=True)
class SignalConfig:
    volume_ratio_threshold: float
    rsi_overbought: float
    rsi_oversold: float


@dataclass(frozen=True)
class MonthlyConfig:
    enabled: bool
    lookback_days: int
    top_n: int
    min_score: float
    board_types: list[str]
    top_board_n: int
    stocks_per_board: int
    max_boards_to_scan: int
    min_amount: float
    require_revenue_growth: bool
    allow_unknown_revenue: bool
    min_revenue_growth_pct: float
    indexes: list[str]


@dataclass(frozen=True)
class MonitorConfig:
    enabled: bool
    lookback_days: int
    state_dir: Path
    max_events_per_stock: int
    push_when_empty: bool
    important_keywords: list[str]


@dataclass(frozen=True)
class RiskConfig:
    enabled: bool
    state_dir: Path
    lookback_days: int
    loss_warn_pct: float
    loss_danger_pct: float
    single_day_drop_pct: float
    volume_ratio_threshold: float
    push_when_empty: bool


@dataclass(frozen=True)
class VolumeMonitorConfig:
    enabled: bool
    source: str
    eastmoney_fallback: bool
    state_dir: Path
    lookback_days: int
    avg_volume_days: int
    high_volume_ratio: float
    low_volume_ratio: float
    price_move_pct: float
    min_progress: float
    push_when_empty: bool


@dataclass(frozen=True)
class CompassConfig:
    enabled: bool
    mode: str
    export_dir: Path
    file_pattern: str
    max_rows: int
    api_base_url: str
    api_endpoint_template: str
    api_timeout: int


@dataclass(frozen=True)
class OverseasAsset:
    symbol: str
    name: str
    market: str
    themes: list[str]


@dataclass(frozen=True)
class OverseasConfig:
    enabled: bool
    history_days: int
    news_limit: int
    indexes: list[OverseasAsset]
    watchlist: list[OverseasAsset]


@dataclass(frozen=True)
class Position:
    code: str
    name: str
    cost_price: float | None
    quantity: float | None
    stop_loss_price: float | None
    note: str


@dataclass(frozen=True)
class DataSourceConfig:
    eastmoney_news: bool
    ths: bool
    compass: CompassConfig


@dataclass(frozen=True)
class AppConfig:
    report: ReportConfig
    monitor: MonitorConfig
    risk: RiskConfig
    volume_monitor: VolumeMonitorConfig
    monthly: MonthlyConfig
    data_sources: DataSourceConfig
    overseas: OverseasConfig
    signals: SignalConfig
    watchlist: list[Stock]
    positions: list[Position]


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    report = data.get("report", {})
    monitor = data.get("monitor", {})
    risk = data.get("risk", {})
    volume_monitor = data.get("volume_monitor", {})
    monthly = data.get("monthly", {})
    data_sources = data.get("data_sources", {})
    compass = data_sources.get("compass", {})
    overseas = data.get("overseas", {})
    signals = data.get("signals", {})
    watchlist = data.get("watchlist", [])
    positions = data.get("positions", [])

    if not watchlist:
        raise ValueError("config.yaml must contain at least one stock in watchlist.")

    return AppConfig(
        report=ReportConfig(
            title=str(report.get("title", "股票信息日报")),
            history_days=int(report.get("history_days", 180)),
            news_limit=int(report.get("news_limit", 5)),
            output_dir=Path(str(report.get("output_dir", "reports"))),
        ),
        monitor=MonitorConfig(
            enabled=bool(monitor.get("enabled", True)),
            lookback_days=int(monitor.get("lookback_days", 14)),
            state_dir=Path(str(monitor.get("state_dir", "state"))),
            max_events_per_stock=int(monitor.get("max_events_per_stock", 8)),
            push_when_empty=bool(monitor.get("push_when_empty", False)),
            important_keywords=_list_value(
                monitor.get(
                    "important_keywords",
                    ["重大", "业绩", "预告", "年报", "中报", "季报", "分红", "回购", "增持", "减持", "重组"],
                )
            ),
        ),
        risk=RiskConfig(
            enabled=bool(risk.get("enabled", True)),
            state_dir=Path(str(risk.get("state_dir", "state"))),
            lookback_days=int(risk.get("lookback_days", 90)),
            loss_warn_pct=float(risk.get("loss_warn_pct", -5)),
            loss_danger_pct=float(risk.get("loss_danger_pct", -10)),
            single_day_drop_pct=float(risk.get("single_day_drop_pct", -4)),
            volume_ratio_threshold=float(risk.get("volume_ratio_threshold", 1.8)),
            push_when_empty=bool(risk.get("push_when_empty", False)),
        ),
        volume_monitor=VolumeMonitorConfig(
            enabled=bool(volume_monitor.get("enabled", True)),
            source=str(volume_monitor.get("source", "ths")).strip().lower(),
            eastmoney_fallback=bool(volume_monitor.get("eastmoney_fallback", False)),
            state_dir=Path(str(volume_monitor.get("state_dir", "state"))),
            lookback_days=int(volume_monitor.get("lookback_days", 40)),
            avg_volume_days=int(volume_monitor.get("avg_volume_days", 20)),
            high_volume_ratio=float(volume_monitor.get("high_volume_ratio", 1.8)),
            low_volume_ratio=float(volume_monitor.get("low_volume_ratio", 0.55)),
            price_move_pct=float(volume_monitor.get("price_move_pct", 2.0)),
            min_progress=float(volume_monitor.get("min_progress", 0.15)),
            push_when_empty=bool(volume_monitor.get("push_when_empty", False)),
        ),
        monthly=MonthlyConfig(
            enabled=bool(monthly.get("enabled", True)),
            lookback_days=int(monthly.get("lookback_days", 22)),
            top_n=int(monthly.get("top_n", 10)),
            min_score=float(monthly.get("min_score", 60)),
            board_types=_list_value(monthly.get("board_types", ["industry", "concept"])),
            top_board_n=int(monthly.get("top_board_n", 8)),
            stocks_per_board=int(monthly.get("stocks_per_board", 5)),
            max_boards_to_scan=int(monthly.get("max_boards_to_scan", 16)),
            min_amount=float(monthly.get("min_amount", 100_000_000)),
            require_revenue_growth=bool(monthly.get("require_revenue_growth", True)),
            allow_unknown_revenue=bool(monthly.get("allow_unknown_revenue", False)),
            min_revenue_growth_pct=float(monthly.get("min_revenue_growth_pct", 0)),
            indexes=_list_value(monthly.get("indexes", ["000001", "399001", "000688"])),
        ),
        data_sources=DataSourceConfig(
            eastmoney_news=bool(data_sources.get("eastmoney_news", True)),
            ths=bool(data_sources.get("ths", True)),
            compass=CompassConfig(
                enabled=bool(compass.get("enabled", True)),
                mode=str(compass.get("mode", "auto")).strip().lower(),
                export_dir=Path(str(compass.get("export_dir", "data/compass"))),
                file_pattern=str(compass.get("file_pattern", "{code}.*")),
                max_rows=int(compass.get("max_rows", 8)),
                api_base_url=str(compass.get("api_base_url", "")).strip(),
                api_endpoint_template=str(compass.get("api_endpoint_template", "")).strip(),
                api_timeout=int(compass.get("api_timeout", 15)),
            ),
        ),
        overseas=OverseasConfig(
            enabled=bool(overseas.get("enabled", True)),
            history_days=int(overseas.get("history_days", 90)),
            news_limit=int(overseas.get("news_limit", 5)),
            indexes=[_overseas_asset(item) for item in overseas.get("indexes", [])],
            watchlist=[_overseas_asset(item) for item in overseas.get("watchlist", [])],
        ),
        signals=SignalConfig(
            volume_ratio_threshold=float(signals.get("volume_ratio_threshold", 1.8)),
            rsi_overbought=float(signals.get("rsi_overbought", 70)),
            rsi_oversold=float(signals.get("rsi_oversold", 30)),
        ),
        watchlist=[
            Stock(
                code=str(item["code"]).zfill(6),
                name=str(item.get("name", item["code"])),
                sectors=_list_value(item.get("sectors")),
                themes=_list_value(item.get("themes")),
            )
            for item in watchlist
        ],
        positions=[_position(item) for item in positions],
    )


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _overseas_asset(item: dict[str, Any]) -> OverseasAsset:
    return OverseasAsset(
        symbol=str(item["symbol"]).strip(),
        name=str(item.get("name", item["symbol"])).strip(),
        market=str(item.get("market", "")).strip(),
        themes=_list_value(item.get("themes")),
    )


def _position(item: dict[str, Any]) -> Position:
    return Position(
        code=str(item["code"]).zfill(6),
        name=str(item.get("name", item["code"])),
        cost_price=_optional_float(item.get("cost_price")),
        quantity=_optional_float(item.get("quantity")),
        stop_loss_price=_optional_float(item.get("stop_loss_price")),
        note=str(item.get("note", "")).strip(),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
