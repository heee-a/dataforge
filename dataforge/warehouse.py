"""星型数仓（SQLite）：维表 dim_city / dim_date + 事实表 fact_weather / fact_air。

写入语义：INSERT OR REPLACE（按自然键幂等）——同一天重跑不产生重复行，
这正是增量管道的幂等性要求。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_city (
    city_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    lat     REAL,
    lon     REAL,
    region  TEXT
);
CREATE TABLE IF NOT EXISTS dim_date (
    date       TEXT PRIMARY KEY,     -- YYYY-MM-DD
    year       INTEGER,
    month      INTEGER,
    day        INTEGER,
    weekday    INTEGER,              -- 0=周一
    is_weekend INTEGER
);
CREATE TABLE IF NOT EXISTS fact_weather (
    city_id TEXT,
    date    TEXT,
    tmax    REAL, tmean REAL, tmin REAL, precip REAL,
    PRIMARY KEY (city_id, date)
);
CREATE TABLE IF NOT EXISTS fact_air (
    city_id TEXT,
    date    TEXT,
    pm25    REAL, pm10 REAL,
    PRIMARY KEY (city_id, date)
);
CREATE TABLE IF NOT EXISTS etl_watermark (
    source     TEXT,
    city_id    TEXT,
    last_date  TEXT,
    updated_at TEXT,
    PRIMARY KEY (source, city_id)
);
CREATE TABLE IF NOT EXISTS dq_results (
    run_id     TEXT,
    rule       TEXT,
    severity   TEXT,
    passed     INTEGER,
    details    TEXT,
    checked_at TEXT
);
"""


class Warehouse:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- 维表 ----
    def upsert_city(self, city_id: str, name: str, lat: float, lon: float,
                    region: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO dim_city VALUES (?, ?, ?, ?, ?)",
            (city_id, name, lat, lon, region))
        self.conn.commit()

    def upsert_date(self, date_text: str) -> None:
        from datetime import date as _d

        d = _d.fromisoformat(date_text)
        self.conn.execute(
            "INSERT OR REPLACE INTO dim_date VALUES (?, ?, ?, ?, ?, ?)",
            (date_text, d.year, d.month, d.day, d.weekday(),
             int(d.weekday() >= 5)))
        self.conn.commit()

    def upsert_dates(self, date_texts: list[str]) -> None:
        """批量写日期维。"""
        from datetime import date as _d

        rows = []
        for date_text in date_texts:
            d = _d.fromisoformat(str(date_text))
            rows.append((str(date_text), d.year, d.month, d.day, d.weekday(),
                         int(d.weekday() >= 5)))
        self.conn.executemany(
            "INSERT OR REPLACE INTO dim_date VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.conn.commit()

    # ---- 事实表（UPSERT 幂等）----
    def upsert_weather(self, rows: list[tuple]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR REPLACE INTO fact_weather VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.conn.commit()
        return self.conn.total_changes - before

    def upsert_air(self, rows: list[tuple]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR REPLACE INTO fact_air VALUES (?, ?, ?, ?)", rows)
        self.conn.commit()
        return self.conn.total_changes - before

    # ---- 水位线 ----
    def get_watermark(self, source: str, city_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_date FROM etl_watermark WHERE source=? AND city_id=?",
            (source, city_id)).fetchone()
        return row["last_date"] if row else None

    def set_watermark(self, source: str, city_id: str, last_date: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO etl_watermark VALUES (?, ?, ?, datetime('now'))",
            (source, city_id, last_date))
        self.conn.commit()

    # ---- 分析查询 ----
    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def close(self) -> None:
        self.conn.close()
