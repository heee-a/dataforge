"""数据质量门禁：规则注册 + 执行器 + 结果落库。

每条规则返回 (passed, details)；severity: error（阻断）/ warn（仅告警）。
规则可组合任意 SQL 与统计断言，与具体数据源解耦。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .warehouse import Warehouse


@dataclass
class RuleResult:
    rule: str
    severity: str            # error / warn
    passed: bool
    details: str


@dataclass
class QualityReport:
    run_id: str
    results: list[RuleResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """所有 error 级规则通过即通过（warn 不阻断）。"""
        return all(r.passed for r in self.results if r.severity == "error")

    def to_rows(self) -> list[tuple]:
        now = datetime.now().isoformat(timespec="seconds")
        return [(self.run_id, r.rule, r.severity, int(r.passed),
                 r.details, now) for r in self.results]

    def summary(self) -> str:
        lines = []
        for r in self.results:
            mark = "✓" if r.passed else ("✗" if r.severity == "error" else "!")
            lines.append(f"  [{mark}] ({r.severity}) {r.rule}: {r.details}")
        return "\n".join(lines)


class QualityRunner:
    def __init__(self, wh: Warehouse):
        self.wh = wh
        self._rules: dict[str, tuple[str, Callable[[Warehouse], RuleResult]]] = {}

    def rule(self, name: str, severity: str = "error") -> Callable:
        """装饰器注册规则；校验函数接收 Warehouse，返回 (passed, details)。"""
        def decorator(fn: Callable[[Warehouse], tuple[bool, str]]):
            def wrapped(wh: Warehouse) -> RuleResult:
                passed, details = fn(wh)
                return RuleResult(name, severity, passed, details)
            self._rules[name] = (severity, wrapped)
            return fn
        return decorator

    def run_all(self) -> QualityReport:
        report = QualityReport(run_id=uuid.uuid4().hex[:8])
        for _name, (severity, wrapped) in self._rules.items():
            report.results.append(wrapped(self.wh))
        report.results.sort(key=lambda r: (r.passed, r.rule))
        self.wh.conn.executemany(
            "INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, ?)", report.to_rows())
        self.wh.conn.commit()
        return report


def default_rules(wh: Warehouse, min_coverage_days: int = 30,
                  freshness_days: int = 7) -> "QualityRunner":
    """内置规则集：完整性 / 值域 / 唯一性 / 新鲜度 / 覆盖率。"""
    qr = QualityRunner(wh)

    def register(name, severity):
        def deco(fn):
            def wrapped(wh):
                passed, details = fn(wh)
                return RuleResult(name, severity, passed, details)
            qr._rules[name] = (severity, wrapped)
            return fn
        return deco

    @register("weather_行数非零", "error")
    def _(wh: Warehouse):
        n = wh.query("SELECT COUNT(*) n FROM fact_weather")[0]["n"]
        return n > 0, f"fact_weather 共 {n} 行"

    @register("weather_值域合理", "error")
    def _(wh: Warehouse):
        row = wh.query("SELECT MIN(tmean) lo, MAX(tmean) hi, "
                       "SUM(CASE WHEN tmean IS NULL THEN 1 ELSE 0 END) nulls "
                       "FROM fact_weather")[0]
        ok = row["lo"] is not None and -50 <= row["lo"] and row["hi"] <= 55 \
            and row["nulls"] == 0
        return ok, f"tmean 范围 [{row['lo']}, {row['hi']}]，空值 {row['nulls']}"

    @register("weather_城市日唯一", "error")
    def _(wh: Warehouse):
        dup = wh.query("SELECT COUNT(*) n FROM (SELECT city_id, date "
                       "FROM fact_weather GROUP BY city_id, date "
                       "HAVING COUNT(*) > 1)")[0]["n"]
        return dup == 0, f"重复的城市-日组合 {dup} 个"

    @register("air_覆盖率", "warn")
    def _(wh: Warehouse):
        row = wh.query("SELECT COUNT(DISTINCT city_id) cities, COUNT(*) n "
                       "FROM fact_air")[0]
        per_city = row["n"] / max(1, row["cities"])
        return per_city >= min_coverage_days, \
            f"air 覆盖 {row['cities']} 城，平均 {per_city:.0f} 天/城（阈值 {min_coverage_days}）"

    @register("air_值域合理", "error")
    def _(wh: Warehouse):
        row = wh.query("SELECT MIN(pm25) lo, MAX(pm25) hi FROM fact_air")[0]
        ok = row["lo"] is not None and 0 <= row["lo"] and row["hi"] <= 500
        return ok, f"pm25 范围 [{row['lo']}, {row['hi']}]"

    @register("freshness_数据新鲜度", "warn")
    def _(wh: Warehouse):
        row = wh.query("SELECT MAX(date) latest FROM fact_weather")[0]
        latest = row["latest"]
        if latest is None:
            return False, "无数据"
        from datetime import date as _d

        age = (_d.today() - _d.fromisoformat(latest)).days
        return age <= freshness_days + 7, \
            f"最新数据 {latest}（滞后 {age} 天，再分析口径容许 {freshness_days}+7 天）"

    return qr
