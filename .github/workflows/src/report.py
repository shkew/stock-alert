from dataclasses import dataclass
from datetime import datetime

from .config import AppConfig, Stock
from .data_sources import fetch_daily_history, fetch_stock_news
from .indicators import enrich_indicators, summarize_technical
from .overseas_source import build_overseas_sections
from .ths_source import fetch_ths_blocks
from .compass_source import fetch_compass_data


@dataclass(frozen=True)
class Report:
    title: str
    markdown: str


def build_report(config: AppConfig) -> Report:
    now = datetime.now()
    title = f"{config.report.title} {now:%Y-%m-%d}"
    sections = [f"# {title}", "", f"生成时间：{now:%Y-%m-%d %H:%M:%S}", ""]

    sections.extend(build_overseas_sections(config.overseas, config.signals))

    for stock in config.watchlist:
        sections.extend(_build_stock_section(stock, config))

    markdown = "\n".join(sections).strip() + "\n"
    config.report.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = config.report.output_dir / f"stock_report_{now:%Y%m%d_%H%M%S}.md"
    output_file.write_text(markdown, encoding="utf-8")
    return Report(title=title, markdown=markdown)


def _build_stock_section(stock: Stock, config: AppConfig) -> list[str]:
    section = [f"## {stock.name}（{stock.code}）", ""]

    try:
        history = fetch_daily_history(stock.code, config.report.history_days)
        enriched = enrich_indicators(history)
        technical = summarize_technical(
            enriched,
            config.signals.volume_ratio_threshold,
            config.signals.rsi_overbought,
            config.signals.rsi_oversold,
        )
    except Exception as exc:
        technical = [f"技术面采集失败：{exc}"]

    section.append("### 技术面")
    section.extend([f"- {item}" for item in technical])
    section.append("")

    section.append("### 消息面")
    if config.data_sources.eastmoney_news:
        news = fetch_stock_news(stock.code, config.report.news_limit)
        if news:
            for item in news:
                prefix = " / ".join(part for part in (item.get("date"), item.get("source")) if part)
                title = item["title"]
                url = item.get("url", "")
                if url:
                    section.append(f"- 东财：{prefix} [{title}]({url})" if prefix else f"- 东财：[{title}]({url})")
                else:
                    section.append(f"- 东财：{prefix} {title}" if prefix else f"- 东财：{title}")
        else:
            section.append("- 东财：暂未获取到相关新闻。")
    else:
        section.append("- 东财新闻源已关闭。")

    if config.data_sources.ths:
        section.append("")
        section.append("### 同花顺数据")
        for block in fetch_ths_blocks(stock.code, stock.name, config.report.news_limit):
            if block.error:
                section.append(f"- {block.title}：采集失败：{block.error}")
            else:
                section.extend([f"- {block.title}：{line}" for line in block.lines])

    if config.data_sources.compass.enabled:
        section.append("")
        section.append("### 指南针数据")
        compass = fetch_compass_data(config.data_sources.compass, stock.code)
        if compass.error:
            section.append(f"- {compass.error}")
        elif compass.source_file:
            section.append(f"- 来源文件：{compass.source_file}")
            section.extend([f"- {line}" for line in compass.lines])
        elif compass.source_name.startswith("api:"):
            section.append(f"- 来源接口：{compass.source_name.removeprefix('api:')}")
            section.extend([f"- {line}" for line in compass.lines])
        else:
            section.append("- 暂无指南针导出数据。")

    section.append("")
    return section
