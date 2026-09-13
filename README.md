# dataforge · 数据管道实战（增量采集 → 数仓 → 质量门禁 → 自动日报）

一个完整的数据工程项目：13 个城市（气象 × 空气质量双源）的**增量采集、
星型数仓、数据质量门禁、自动日报**，全部跑通真实公开数据。

## 架构

```
Open-Meteo（气象 + 空气质量 API）
   │  sources.py：按水位线增量请求 [last+1, today-7]
   ▼
extract（增量抽取）──► warehouse（SQLite 星型数仓）
   │                    ├─ dim_city / dim_date 维表
   │                    ├─ fact_weather / fact_air 事实表（UPSERT 幂等）
   │                    └─ etl_watermark 水位线 / dq_results 质量结果
   ▼
quality（数据质量门禁）── 规则引擎：完整性/值域/唯一性/新鲜度/覆盖率
   │                     error 级不通过即门禁失败，结果落库
   ▼
report（自动日报）── Markdown + 自包含 HTML（KPI/排名/走势图）
```

## 实战要点（与玩具 ETL 的区别）

| 概念 | 实现 | 为什么重要 |
|---|---|---|
| **增量采集** | `etl_watermark` 表记录每源每城的最后日期，只请求缺失区间 | 长期运行成本与新数据量成正比，而非全量重扫 |
| **幂等性** | 事实表按自然键 `INSERT OR REPLACE`，重跑不产生重复行 | 管道失败重跑是常态，幂等是底线 |
| **数据质量门禁** | 规则装饰器注册（error 阻断 / warn 告警），结果落 `dq_results` | "能跑"不等于"数据对"，门禁让坏数据显形 |
| **新鲜度口径** | 再分析数据有 7 天滞后，freshness 规则按"滞后 ≤7+7 天"放行 | 质量规则必须理解数据源的物理特性 |
| ** 配置外置** | 数据库路径、输出目录均由代码参数/约定控制 | 环境可移植 |

## 实测运行记录

```bash
python -m dataforge run
# [weather] 北京: 增量 60 行（2026-07-09 ~ 2026-09-06）   ← 13 城 × 2 源
# 入库完成: weather +780 行, air +780 行
# === 数据质量门禁 === 全部通过（run_id=97fe29b1）
#   [✓] air_值域合理: pm25 范围 [3.54, 220.11]
#   [✓] freshness_数据新鲜度: 最新数据 2026-09-06（滞后 7 天）
# 日报: reports/daily_report.md + daily_report.html

# 第二次运行——增量与幂等实测：
python -m dataforge extract
# [weather] 北京: 已是最新，跳过          ← 水位线生效，零重复请求
```

## 数据字典

| 表 | 字段 | 说明 |
|---|---|---|
| dim_city | city_id, name, lat, lon, region | 城市维表 |
| dim_date | date, year, month, day, weekday, is_weekend | 日期维表 |
| fact_weather | city_id, date, tmax/tmean/tmin/precip | 气象事实（日粒度） |
| fact_air | city_id, date, pm25, pm10 | 空气质量事实（日粒度） |
| etl_watermark | source, city_id, last_date | 增量水位线 |
| dq_results | run_id, rule, severity, passed, details, checked_at | 质量检查历史 |

## 快速开始

```bash
pip install -e .
python -m dataforge run            # 全流程（真实采集，约 1 分钟）
python -m dataforge extract        # 只做增量采集
python -m dataforge quality        # 只跑质量门禁
python -m dataforge report         # 只生成日报
python -m dataforge run --full-refresh   # 忽略水位线强制重采最近 60 天
```

## 局限

- SQLite 单机仓库，适合演示与个人规模；分布式需要换存储与调度层；
- 采集调度靠手动/cron，未内置常驻调度器（可对接 crontab 每日运行）；
- PM2.5 为 CAMS 再分析估计值，绝对量级有系统偏差。

## License

[MIT](LICENSE)
