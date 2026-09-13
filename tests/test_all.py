"""dataforge 测试：增量逻辑、UPSERT 幂等、质量规则、日报生成（全部离线）。"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dataforge.quality import default_rules          # noqa: E402
from dataforge.report import build_report            # noqa: E402
from dataforge.sources import missing_interval       # noqa: E402
from dataforge.warehouse import Warehouse            # noqa: E402

from datetime import date, timedelta                            # noqa: E402


# ---------------- 增量逻辑 ----------------
def test_missing_interval_from_scratch():
    today = date(2026, 9, 13)
    start, end = missing_interval(None, today=today)
    assert (end - start).days == 59                     # 60 天窗口（含首尾 59 天间隔）
    assert end < today


def test_missing_interval_incremental():
    today = date(2026, 9, 13)
    start, end = missing_interval(date(2026, 9, 1), today=today)
    assert (start, end) == (date(2026, 9, 2), date(2026, 9, 6))


def test_missing_interval_up_to_date():
    assert missing_interval(date(2026, 9, 10), today=date(2026, 9, 13)) is None


# ---------------- 数仓幂等 ----------------
@pytest.fixture()
def wh(tmp_path):
    return Warehouse(tmp_path / "w.db")


def test_upsert_idempotent(wh):
    wh.upsert_city("beijing", "北京", 39.9, 116.4, "北方")
    wh.upsert_dates(["2026-09-01", "2026-09-02"])
    rows = [("beijing", "2026-09-01", 30.0, 25.0, 20.0, 0.0),
            ("beijing", "2026-09-01", 30.0, 25.0, 20.0, 0.0)]  # 同键两行
    wh.upsert_weather(rows)
    n = wh.query("SELECT COUNT(*) n FROM fact_weather")[0]["n"]
    assert n == 1                                       # UPSERT 幂等，不产生重复
    wh.upsert_weather([("beijing", "2026-09-01", 31.0, 26.0, 21.0, 0.5)])
    v = wh.query("SELECT tmean, precip FROM fact_weather")[0]
    assert v["tmean"] == 26.0 and v["precip"] == 0.5    # 同键覆盖更新


def test_watermark_roundtrip(wh):
    assert wh.get_watermark("weather", "beijing") is None
    wh.set_watermark("weather", "beijing", "2026-09-06")
    assert wh.get_watermark("weather", "beijing") == "2026-09-06"


# ---------------- 质量规则 ----------------
def test_quality_rules_pass_on_clean_data(wh):
    wh.upsert_city("a", "城市甲", 30.0, 110.0)
    days = [(date.today() - timedelta(days=i)).isoformat()
            for i in range(29, -1, -1)]
    wh.upsert_dates(days)
    wh.upsert_weather([("a", d, 25.0, 20.0, 15.0, 0.0) for d in days])
    wh.upsert_air([("a", d, 20.0, 40.0) for d in days])
    report = default_rules(wh).run_all()
    assert report.passed
    assert all(r.passed for r in report.results)


def test_quality_rules_catch_dirty_data(wh):
    wh.upsert_city("a", "城市甲", 30.0, 110.0)
    wh.upsert_dates(["2026-08-01"])
    wh.upsert_weather([("a", "2026-08-01", 999.0, 80.0, 15.0, 0.0)])  # 超值域
    report = default_rules(wh).run_all()
    assert not report.passed                            # error 级规则拦截
    broken = [r.rule for r in report.results if not r.passed]
    assert "weather_值域合理" in broken


def test_dq_results_persisted(wh):
    wh.upsert_city("a", "城市甲", 30.0, 110.0)
    wh.upsert_dates(["2026-08-01"])
    wh.upsert_weather([("a", "2026-08-01", 25.0, 20.0, 15.0, 0.0)])
    report = default_rules(wh).run_all()
    rows = wh.query("SELECT * FROM dq_results WHERE run_id=?",
                    (report.run_id,))
    assert len(rows) == len(report.results)


# ---------------- 日报 ----------------
def test_build_report(tmp_path, wh):
    wh.upsert_city("a", "城市甲", 30.0, 110.0)
    days = [(date.today() - timedelta(days=i)).isoformat()
            for i in range(29, -1, -1)]
    wh.upsert_dates(days)
    wh.upsert_weather([("a", d, 25.0, 20.0, 15.0, 0.0) for d in days])
    wh.upsert_air([("a", d, 20.0, 40.0) for d in days])
    md, html = build_report(wh, tmp_path / "out")
    assert md.exists() and html.exists()
    text = md.read_text(encoding="utf-8")
    assert "dataforge 数据日报" in text and "城市甲" in text
    html_text = html.read_text(encoding="utf-8")
    assert "Plotly" in html_text or "<table>" in html_text
