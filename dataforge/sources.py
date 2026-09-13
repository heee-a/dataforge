"""数据源适配器：Open-Meteo 天气 / 空气质量，支持增量区间请求。

增量逻辑（watermark）：
- etl_watermark 表记录每个 (source, city) 已采集到的最后日期；
- extract 只请求 [watermark+1, today-delay] 的缺失区间；
- 区间为空（已是最新）则跳过请求——长期运行的成本只与新数据量成正比。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

WEATHER_API = "https://archive-api.open-meteo.com/v1/archive"
AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"

# 13 城为气象与空气两个数据源的城市交集（坐标一致）
CITIES: dict[str, tuple[float, float]] = {
    "北京": (39.90, 116.41), "上海": (31.23, 121.47), "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06), "成都": (30.57, 104.07), "重庆": (29.56, 106.55),
    "武汉": (30.59, 114.31), "西安": (34.34, 108.94), "杭州": (30.27, 120.16),
    "南京": (32.06, 118.80), "哈尔滨": (45.80, 126.53), "乌鲁木齐": (43.83, 87.62),
    "兰州": (36.06, 103.83),
}

ARCHIVE_DELAY_DAYS = 7   # 再分析数据滞后天数，freshness 规则据此放行


@dataclass
class ExtractResult:
    source: str
    city: str
    start: date
    end: date
    df: pd.DataFrame

    @property
    def is_empty(self) -> bool:
        return self.df.empty


def _daterange_end(today: date | None = None) -> date:
    """再分析数据存在滞后，可采区间终点 = 今天 - ARCHIVE_DELAY_DAYS。"""
    return (today or date.today()) - timedelta(days=ARCHIVE_DELAY_DAYS)


def missing_interval(last_date: date | None, today: date | None = None) -> tuple[date, date] | None:
    """计算 [last+1, 可采终点]；已是最新返回 None。"""
    end = _daterange_end(today)
    start = (last_date + timedelta(days=1)) if last_date else end - timedelta(days=59)
    if start > end:
        return None
    return start, end


def extract_weather(f, city: str, lat: float, lon: float,
                    start: date, end: date) -> ExtractResult:
    data = f.get_json(WEATHER_API, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "daily": ("temperature_2m_max,temperature_2m_mean,"
                  "temperature_2m_min,precipitation_sum"),
        "timezone": "Asia/Shanghai"})
    d = data["daily"]
    df = pd.DataFrame({
        "city": city, "date": pd.to_datetime(d["time"]),
        "tmax": d["temperature_2m_max"], "tmean": d["temperature_2m_mean"],
        "tmin": d["temperature_2m_min"], "precip": d["precipitation_sum"],
    }).dropna(subset=["tmean"])
    return ExtractResult("weather", city, start, end, df)


def extract_air(f, city: str, lat: float, lon: float,
                start: date, end: date) -> ExtractResult:
    data = f.get_json(AIR_API, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": "pm2_5,pm10", "timezone": "Asia/Shanghai"})
    h = data["hourly"]
    df = pd.DataFrame({"city": city, "time": pd.to_datetime(h["time"]),
                       "pm25": h["pm2_5"], "pm10": h["pm10"]}).dropna(subset=["pm25"])
    if df.empty:
        return ExtractResult("air", city, start, end, df)
    df["date"] = df["time"].dt.date
    daily = (df.groupby("date").agg(pm25=("pm25", "mean"), pm10=("pm10", "mean"),
                                    hours=("pm25", "count"))
             .reset_index()
             .query("hours >= 12")                      # 当日有效小时过半才保留
             .drop(columns="hours"))
    daily["city"] = city
    return ExtractResult("air", city, start, end, daily)
