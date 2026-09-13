"""dataforge 命令行：增量采集 → 入库 → 质量门禁 → 日报。

用法:
    python -m dataforge run                      # 全流程
    python -m dataforge extract                  # 只做增量采集+入库
    python -m dataforge quality                  # 只跑质量门禁
    python -m dataforge report                   # 只生成日报
"""

from __future__ import annotations

import argparse
from pathlib import Path

from datap.fetching import Fetcher

from .quality import default_rules
from .report import build_report
from .sources import CITIES, extract_air, extract_weather, missing_interval
from .warehouse import Warehouse

DB = Path("warehouse/dataforge.db")
REPORT_DIR = Path("reports")


def get_warehouse() -> Warehouse:
    DB.parent.mkdir(parents=True, exist_ok=True)
    return Warehouse(DB)


def cmd_extract(wh: Warehouse, f: Fetcher) -> None:
    total = {"weather": 0, "air": 0}
    for city, (lat, lon) in CITIES.items():
        for source, extract in (("weather", extract_weather), ("air", extract_air)):
            last = wh.get_watermark(source, city)
            interval = missing_interval(
                __import__("datetime").date.fromisoformat(last) if last else None)
            if interval is None:
                print(f"  [{source}] {city}: 已是最新，跳过")
                continue
            start, end = interval
            result = extract(f, city, lat, lon, start, end)
            if result.is_empty:
                print(f"  [{source}] {city}: 区间 {start}~{end} 无数据")
                continue
            if source == "weather":
                n = wh.upsert_weather(
                    [(city, r["date"].date().isoformat(), r["tmax"], r["tmean"],
                      r["tmin"], r["precip"]) for _, r in result.df.iterrows()])
            else:
                n = wh.upsert_air(
                    [(city, str(r["date"]), round(r["pm25"], 2), round(r["pm10"], 2))
                     for _, r in result.df.iterrows()])
            total[source] += n
            last_in_data = result.df["date"].max()
            last_iso = (last_in_data.date().isoformat()
                        if hasattr(last_in_data, "date") else str(last_in_data))
            wh.set_watermark(source, city, last_iso)
            dates = [d.date().isoformat() if hasattr(d, "date") else str(d)
                     for d in result.df["date"]]
            wh.upsert_dates(dates)
            print(f"  [{source}] {city}: 增量 {n} 行（{start} ~ {end}）")
    print(f"入库完成: weather +{total['weather']} 行, air +{total['air']} 行")


def cmd_quality(wh: Warehouse) -> bool:
    runner = default_rules(wh)
    report = runner.run_all()
    print("=== 数据质量门禁 ===")
    print(report.summary())
    verdict = "通过" if report.passed else "未通过（存在 error 级问题）"
    print(f"门禁结论: {verdict}（run_id={report.run_id}）")
    return report.passed


def cmd_report(wh: Warehouse) -> None:
    md, html = build_report(wh, REPORT_DIR, chart_rel="charts/pm_monthly.png")
    print(f"日报: {md}\n      {html}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="dataforge", description="数据管道实战：增量采集→数仓→质量→日报")
    ap.add_argument("command", choices=["run", "extract", "quality", "report"])
    ap.add_argument("--full-refresh", action="store_true",
                    help="忽略水位线，重采最近 60 天（UPSERT 幂等）")
    args = ap.parse_args()

    wh = get_warehouse()
    for city, (lat, lon) in CITIES.items():
        wh.upsert_city(city.replace(" ", ""), city, lat, lon, "")

    if args.full_refresh:  # 回拨全部水位线以强制重采
        wh.conn.execute("DELETE FROM etl_watermark")
        wh.conn.commit()

    if args.command in ("run", "extract"):
        f = Fetcher(cache_dir=Path(".cache"), min_interval=1.5, max_retries=5)
        cmd_extract(wh, f)
    if args.command in ("run", "quality"):
        if not cmd_quality(wh):
            print("（error 级质量问题存在，日报仍会生成并保留质量记录供排查）")
    if args.command in ("run", "report"):
        cmd_report(wh)


if __name__ == "__main__":
    main()
