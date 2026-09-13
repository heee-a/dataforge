"""日报生成：Markdown + 自包含 HTML（内嵌图表）。"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from datap.plotstyle import setup_style  # noqa: E402

from .warehouse import Warehouse  # noqa: E402


def _fetch_frames(wh: Warehouse) -> tuple[pd.DataFrame, pd.DataFrame]:
    wx = pd.read_sql_query(
        "SELECT c.name, f.date, f.tmean, f.precip FROM fact_weather f "
        "JOIN dim_city c USING (city_id) ORDER BY f.date", wh.conn)
    air = pd.read_sql_query(
        "SELECT c.name, f.date, f.pm25 FROM fact_air f "
        "JOIN dim_city c USING (city_id) ORDER BY f.date", wh.conn)
    for df in (wx, air):
        df["date"] = pd.to_datetime(df["date"])
    return wx, air


def _chart_pm_monthly(air: pd.DataFrame, out: Path) -> str:
    setup_style()
    monthly = (air.groupby([air["date"].dt.to_period("M").astype(str), "name"])
               ["pm25"].mean().unstack())
    fig, ax = plt.subplots(figsize=(11, 5))
    for city in monthly.columns:
        ax.plot(monthly.index, monthly[city], marker="o", ms=3, label=city)
    ax.set_ylabel("月均 PM2.5 µg/m³")
    ax.set_title("各城市月均 PM2.5 走势")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(ncols=4, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out.name


def build_report(wh: Warehouse, out_dir: Path,
                 chart_rel: str = "charts/pm_monthly.png") -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "charts").mkdir(exist_ok=True)
    wx, air = _fetch_frames(wh)

    kpi = {
        "城市数": wx["name"].nunique(),
        "气象覆盖天数": wx["date"].nunique(),
        "最新数据日": str(wx["date"].max().date()),
        "期间均温": round(wx["tmean"].mean(), 1),
        "期间 PM2.5 均值": round(air["pm25"].mean(), 1) if len(air) else None,
    }
    ranking = (air.groupby("name")["pm25"].mean().sort_values(ascending=False)
               .round(1))
    chart_file = _chart_pm_monthly(air, out_dir / chart_rel.lstrip("./")) \
        if len(air) else ""

    md = ["# dataforge 数据日报", "",
          f"> 生成时间：{pd.Timestamp.now():%Y-%m-%d %H:%M}　|　"
          f"数据源：Open-Meteo（CAMS 再分析）", "",
          "## KPI", ""]
    md += [f"- {k}: {v}" for k, v in kpi.items()]
    md += ["", "## 城市 PM2.5 排名（期间均值）", "",
           "| 城市 | PM2.5 µg/m³ |", "|---|---|"]
    md += [f"| {c} | {v} |" for c, v in ranking.items()]
    md += ["", f"![PM2.5 走势]({chart_rel})" if chart_file else "", ""]
    md_path = out_dir / "daily_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>dataforge 日报</title><style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f7f8fa; }}
header {{ background: #14532d; color: white; padding: 20px 36px; }}
header h1 {{ margin: 0; font-size: 20px; }}
main {{ max-width: 980px; margin: 20px auto; background: white; padding: 8px 28px;
       border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #e5e7eb;
padding: 6px 14px; }} th {{ background: #f3f4f6; }}
</style></head><body>
<header><h1>dataforge 数据日报</h1><p>生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M} ·
Open-Meteo（CAMS 再分析）</p></header>
<main>
<h2>KPI</h2><ul>{''.join(f'<li><b>{k}</b>: {v}</li>' for k, v in kpi.items())}</ul>
<h2>城市 PM2.5 排名</h2>
<table><tr><th>城市</th><th>PM2.5 µg/m³</th></tr>
{''.join(f'<tr><td>{c}</td><td>{v}</td></tr>' for c, v in ranking.items())}
</table>
<img src="{chart_rel}" style="max-width:100%">
</main></body></html>"""
    html_path = out_dir / "daily_report.html"
    html_path.write_text(html, encoding="utf-8")
    return md_path, html_path
