import argparse
import os
from pathlib import Path

from .config import load_config
from .event_monitor import run_event_monitor
from .report import build_report
from .push import push_markdown
from .risk_monitor import run_risk_monitor
from .volume_monitor import run_volume_monitor


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect stock news and technical signals, then push to WeChat.")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml.")
    parser.add_argument("--dry-run", action="store_true", help="Only print and save the report, do not push.")
    parser.add_argument("--monthly", action="store_true", help="Generate full-market board scan and stock candidate report.")
    parser.add_argument("--market-scan", action="store_true", help="Alias for --monthly.")
    parser.add_argument("--monitor", action="store_true", help="Push only new important news, announcements, and financial-report events.")
    parser.add_argument("--risk-monitor", action="store_true", help="Push account-position risk alerts.")
    parser.add_argument("--volume-monitor", action="store_true", help="Push intraday abnormal volume alerts.")
    parser.add_argument("--no-state", action="store_true", help="Do not update incremental monitor state.")
    parser.add_argument("--ignore-state", action="store_true", help="Ignore dedupe state and include matching events for preview/replay.")
    args = parser.parse_args()

    load_env_file(Path(".env"))

    config = load_config(Path(args.config))
    if args.monthly or args.market_scan:
        from .monthly_analysis import save_monthly_report

        markdown = save_monthly_report(config)
        print(markdown)
        return

    if args.monitor:
        result = run_event_monitor(config, update_state=not args.no_state, ignore_state=args.ignore_state)
        print(result.markdown)
        if not args.dry_run and (result.new_events or config.monitor.push_when_empty):
            push_markdown(result.title, result.brief)
        return

    if args.risk_monitor:
        result = run_risk_monitor(config, update_state=not args.no_state)
        print(result.markdown)
        if not args.dry_run and (result.alerts or config.risk.push_when_empty):
            push_markdown(result.title, result.markdown)
        return

    if args.volume_monitor:
        result = run_volume_monitor(config, update_state=not args.no_state)
        print(result.markdown)
        if not args.dry_run and (result.alerts or config.volume_monitor.push_when_empty):
            push_markdown(result.title, result.brief)
        return

    report = build_report(config)
    print(report.markdown)

    if not args.dry_run:
        push_markdown(report.title, report.markdown)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
