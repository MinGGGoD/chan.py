from __future__ import annotations

import re
import sys
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from pyecharts import options as opts
from pyecharts.charts import Kline

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_FIELD, DATA_SRC, KL_TYPE
from Plot.PlotMeta import CChanPlotMeta


app = Flask(__name__)

LEVEL_MAP = {
    "day": KL_TYPE.K_DAY,
    "week": KL_TYPE.K_WEEK,
    "month": KL_TYPE.K_MON,
}

LEVEL_LABEL = {
    "day": "日线",
    "week": "周线",
    "month": "月线",
}

FULL_CODE_RE = re.compile(r"^(sh|sz)\.\d{6}$", re.IGNORECASE)
SHORT_CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MA_WINDOWS = (5, 10, 20, 30)
ECHARTS_JS_URL = "/static/vendor/echarts.min.js"


def default_start() -> str:
    return (date.today() - timedelta(days=365 * 3)).isoformat()


def default_end() -> str:
    return date.today().isoformat()


def normalize_date(value: str | None, fallback: str, field_name: str) -> str:
    if not value:
        return fallback
    value = value.strip()
    if not DATE_RE.match(value):
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def normalize_stock_code(value: str | None) -> str:
    code = (value or "000001").strip().lower()
    if FULL_CODE_RE.match(code):
        return code
    if not SHORT_CODE_RE.match(code):
        raise ValueError("股票代码请输入 6 位数字，如 000001 或 600519")

    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    if code.startswith(("4", "8")):
        raise ValueError("当前 Baostock 绘图首版只支持沪深 A 股，暂不支持北交所代码")
    raise ValueError("无法根据该代码判断交易所，请检查是否为沪深 A 股 6 位代码")


def normalize_request_args(args) -> tuple[str, str, str, str]:
    code = normalize_stock_code(args.get("code"))

    level = args.get("level", "day").strip().lower()
    if level not in LEVEL_MAP:
        raise ValueError("级别只支持 day、week、month")

    start = normalize_date(args.get("start"), default_start(), "start")
    end = normalize_date(args.get("end"), default_end(), "end")
    if start > end:
        raise ValueError("start 不能晚于 end")

    return code, level, start, end


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def moving_average(values: list[float], window: int) -> list[float | None]:
    res: list[float | None] = []
    rolling_sum = 0.0
    for idx, value in enumerate(values):
        rolling_sum += value
        if idx >= window:
            rolling_sum -= values[idx - window]
        if idx + 1 < window:
            res.append(None)
        else:
            res.append(round(rolling_sum / window, 4))
    return res


def build_date_ticks(candles: list[dict[str, Any]], level: str) -> list[dict[str, Any]]:
    if not candles:
        return []

    target_count = 14 if level == "day" else 16
    tick_indexes: list[int] = []
    seen_periods = set()
    for idx, candle in enumerate(candles):
        parts = str(candle["time"]).split("/")
        if len(parts) < 3:
            continue
        year, month, day = parts[:3]
        period_key = year if level == "month" else f"{year}-{month}"
        if period_key not in seen_periods:
            seen_periods.add(period_key)
            tick_indexes.append(idx)

    if not tick_indexes:
        step = max(1, (len(candles) + target_count - 1) // target_count)
        tick_indexes = list(range(0, len(candles), step))
    elif len(tick_indexes) > target_count:
        step = max(1, (len(tick_indexes) + target_count - 1) // target_count)
        tick_indexes = [tick_idx for idx, tick_idx in enumerate(tick_indexes) if idx % step == 0]

    last_idx = len(candles) - 1
    if last_idx not in tick_indexes:
        tick_indexes.append(last_idx)

    ticks = []
    last_year = ""
    for idx in tick_indexes:
        candle = candles[idx]
        parts = str(candle["time"]).split("/")
        if len(parts) < 3:
            label = str(candle["time"])
        else:
            year, month, day = parts[:3]
            if level == "month":
                label = f"{year}/{month}" if year != last_year else month
            elif year != last_year:
                label = f"{year}/{month}"
            elif level == "day":
                label = f"{month}月"
            else:
                label = f"{month}/{day}"
            last_year = year
        ticks.append({"value": candle["idx"], "label": label})
    return ticks


def serialize_klu(klu) -> dict[str, Any]:
    return {
        "idx": klu.idx,
        "time": klu.time.to_str(),
        "open": safe_float(klu.open),
        "high": safe_float(klu.high),
        "low": safe_float(klu.low),
        "close": safe_float(klu.close),
        "volume": safe_float(klu.trade_info.metric.get(DATA_FIELD.FIELD_VOLUME)),
        "turnover": safe_float(klu.trade_info.metric.get(DATA_FIELD.FIELD_TURNOVER)),
        "turnover_rate": safe_float(klu.trade_info.metric.get(DATA_FIELD.FIELD_TURNRATE)),
    }


def serialize_line(line_meta) -> dict[str, Any]:
    return {
        "idx": line_meta.idx,
        "x0": line_meta.begin_x,
        "x1": line_meta.end_x,
        "y0": safe_float(line_meta.begin_y),
        "y1": safe_float(line_meta.end_y),
        "is_sure": bool(line_meta.is_sure),
    }


def serialize_zs(zs_meta, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "begin": zs_meta.begin,
        "end": zs_meta.end,
        "low": safe_float(zs_meta.low),
        "high": safe_float(zs_meta.high),
        "is_sure": bool(zs_meta.is_sure),
        "is_onebi_zs": bool(zs_meta.is_onebi_zs),
    }


def serialize_bsp(bsp_meta) -> dict[str, Any]:
    return {
        "x": bsp_meta.x,
        "y": safe_float(bsp_meta.y),
        "label": bsp_meta.desc(),
        "type": bsp_meta.type,
        "is_buy": bool(bsp_meta.is_buy),
        "is_seg": bool(bsp_meta.is_seg),
    }


def build_config() -> CChanConfig:
    return CChanConfig({
        "bi_strict": True,
        "trigger_step": False,
        "skip_step": 0,
        "divergence_rate": float("inf"),
        "bsp2_follow_1": False,
        "bsp3_follow_1": False,
        "min_zs_cnt": 0,
        "bs1_peak": False,
        "macd_algo": "peak",
        "bs_type": "1,2,3a,1p,2s,3b",
        "print_warning": False,
        "zs_algo": "normal",
    })


def line_segment_data(items: list[dict[str, Any]]) -> list[Any]:
    data = []
    for item in items:
        data.append([item["x0"], item["y0"]])
        data.append([item["x1"], item["y1"]])
        data.append(None)
    return data


def signal_series(name: str, signals: list[dict[str, Any]], is_buy: bool, is_seg: bool) -> dict[str, Any]:
    filtered = [
        item for item in signals
        if item["is_buy"] is is_buy and item["is_seg"] is is_seg
    ]
    return {
        "type": "scatter",
        "name": name,
        "data": [[item["x"], item["y"], item["label"]] for item in filtered],
        "symbol": "triangle",
        "symbolRotate": 0 if is_buy else 180,
        "symbolSize": 11 if not is_seg else 12,
        "itemStyle": {
            "color": "#d62728" if is_buy else "#188038",
            "borderColor": "#ffffff",
            "borderWidth": 1,
        },
        "label": {
            "show": True,
            "formatter": "{@[2]}",
            "position": "bottom" if is_buy else "top",
            "fontSize": 11,
            "color": "#111827",
        },
        "tooltip": {"show": False},
    }


def mark_area_data(items: list[dict[str, Any]]) -> list[Any]:
    return [
        [
            {"xAxis": item["begin"], "yAxis": item["high"]},
            {"xAxis": item["end"], "yAxis": item["low"]},
        ]
        for item in items
    ]


def build_pyecharts_option(payload: dict[str, Any]) -> dict[str, Any]:
    candles = payload["candles"]
    elements = payload["elements"]
    x_axis = [item["idx"] for item in candles]
    kline_data = [[item["open"], item["close"], item["low"], item["high"]] for item in candles]

    chart = (
        Kline()
        .add_xaxis(x_axis)
        .add_yaxis(
            "K线",
            kline_data,
            itemstyle_opts=opts.ItemStyleOpts(
                color="#d62728",
                color0="#188038",
                border_color="#d62728",
                border_color0="#188038",
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(
                title=f"{payload['code']} {payload['level_label']} 缠论图",
                pos_left="12px",
                pos_top="8px",
                title_textstyle_opts=opts.TextStyleOpts(font_size=14, color="#172033"),
            ),
            legend_opts=opts.LegendOpts(
                type_="scroll",
                pos_top="8px",
                pos_right="10px",
                orient="horizontal",
            ),
            tooltip_opts=opts.TooltipOpts(
                is_show=True,
                trigger="axis",
                axis_pointer_type="cross",
                is_show_content=False,
            ),
            xaxis_opts=opts.AxisOpts(
                type_="category",
                boundary_gap=True,
                axislabel_opts=opts.LabelOpts(color="#172033", font_size=11),
                splitline_opts=opts.SplitLineOpts(is_show=False),
            ),
            yaxis_opts=opts.AxisOpts(
                is_scale=True,
                axislabel_opts=opts.LabelOpts(color="#172033"),
                splitline_opts=opts.SplitLineOpts(is_show=False),
            ),
            datazoom_opts=[
                opts.DataZoomOpts(
                    is_show=False,
                    type_="inside",
                    range_start=0,
                    range_end=100,
                    xaxis_index=[0],
                ),
                opts.DataZoomOpts(
                    is_show=True,
                    type_="slider",
                    pos_bottom="8px",
                    height=20,
                    range_start=0,
                    range_end=100,
                    xaxis_index=[0],
                ),
            ],
        )
    )

    option = json.loads(chart.dump_options())
    option["color"] = [
        "#d62728",
        "#f59e0b",
        "#2563eb",
        "#7c3aed",
        "#0f766e",
        "#111827",
        "#e11d48",
        "#2563eb",
        "#7c3aed",
        "#d62728",
        "#188038",
    ]
    option["grid"] = {
        "left": 56,
        "right": 26,
        "top": 54,
        "bottom": 56,
        "containLabel": True,
    }
    option["animation"] = False

    ma_colors = {
        "ma5": "#f59e0b",
        "ma10": "#2563eb",
        "ma20": "#7c3aed",
        "ma30": "#0f766e",
    }
    for ma_name, color in ma_colors.items():
        option["series"].append({
            "type": "line",
            "name": ma_name.upper(),
            "data": payload["ma"][ma_name],
            "showSymbol": False,
            "smooth": True,
            "lineStyle": {"width": 1.2, "color": color},
            "itemStyle": {"color": color},
            "endLabel": {"show": False},
            "tooltip": {"show": False},
        })

    option["series"].extend([
        {
            "type": "line",
            "name": "笔",
            "data": line_segment_data(elements["bi"]),
            "showSymbol": False,
            "connectNulls": False,
            "lineStyle": {"width": 1.8, "color": "#111827"},
            "tooltip": {"show": False},
        },
        {
            "type": "line",
            "name": "线段",
            "data": line_segment_data(elements["seg"]),
            "showSymbol": False,
            "connectNulls": False,
            "lineStyle": {"width": 3, "color": "#e11d48"},
            "tooltip": {"show": False},
        },
        {
            "type": "line",
            "name": "中枢",
            "data": [],
            "markArea": {
                "silent": True,
                "itemStyle": {"color": "rgba(37, 99, 235, 0.16)"},
                "data": mark_area_data(elements["zs"]),
            },
            "tooltip": {"show": False},
        },
        {
            "type": "line",
            "name": "线段中枢",
            "data": [],
            "markArea": {
                "silent": True,
                "itemStyle": {"color": "rgba(124, 58, 237, 0.14)"},
                "data": mark_area_data(elements["seg_zs"]),
            },
            "tooltip": {"show": False},
        },
        signal_series("买点", elements["bsp"], True, False),
        signal_series("卖点", elements["bsp"], False, False),
        signal_series("线段买点", elements["seg_bsp"], True, True),
        signal_series("线段卖点", elements["seg_bsp"], False, True),
    ])

    return option


@lru_cache(maxsize=32)
def build_chart_payload(code: str, level: str, start: str, end: str) -> dict[str, Any]:
    chan = CChan(
        code=code,
        begin_time=start,
        end_time=end,
        data_src=DATA_SRC.BAO_STOCK,
        lv_list=[LEVEL_MAP[level]],
        config=build_config(),
        autype=AUTYPE.QFQ,
    )
    kl_list = chan[0]
    meta = CChanPlotMeta(kl_list)
    candles = [serialize_klu(klu) for klu in meta.klu_iter()]
    if not candles:
        raise ValueError("没有获取到 K 线数据，请检查股票代码或日期范围")

    closes = [float(item["close"]) for item in candles]
    ma = {f"ma{window}": moving_average(closes, window) for window in MA_WINDOWS}

    payload = {
        "success": True,
        "code": code,
        "level": level,
        "level_label": LEVEL_LABEL[level],
        "start": start,
        "end": end,
        "candles": candles,
        "ma": ma,
        "elements": {
            "bi": [serialize_line(bi) for bi in meta.bi_list],
            "seg": [serialize_line(seg) for seg in meta.seg_list],
            "zs": [serialize_zs(zs, "bi") for zs in meta.zs_lst],
            "seg_zs": [serialize_zs(zs, "seg") for zs in meta.segzs_lst],
            "bsp": [serialize_bsp(bsp) for bsp in meta.bs_point_lst],
            "seg_bsp": [serialize_bsp(bsp) for bsp in meta.seg_bsp_lst],
        },
    }
    payload["date_ticks"] = build_date_ticks(candles, level)
    payload["chart_options"] = build_pyecharts_option(payload)
    return payload


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_code="000001",
        default_start=default_start(),
        default_end=default_end(),
        echarts_js_url=ECHARTS_JS_URL,
    )


@app.get("/api/chart")
def api_chart():
    try:
        code, level, start, end = normalize_request_args(request.args)
        return jsonify(build_chart_payload(code, level, start, end))
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
