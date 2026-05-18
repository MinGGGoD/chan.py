from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Chan import CChan
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Web.app import build_chart_payload_from_chan, build_config


DEFAULT_OUTPUT_DIR = ROOT_DIR / "Web" / "data" / "scan_results"
DEFAULT_START_DATE = "2024-01-01"
DEFAULT_RECENT_COUNT = 7


@dataclass(frozen=True)
class StockRecord:
    code: str
    name: str
    market: str
    latest_price: float | None = None
    change_percent: float | None = None

    @property
    def full_code(self) -> str:
        return f"{self.market.lower()}.{self.code}" if self.market else self.code


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def market_from_code(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8")) or code.startswith("920"):
        return "BJ"
    return ""


def market_label(market: str) -> str:
    return {"SH": "沪市", "SZ": "深市", "BJ": "北交所"}.get(market.upper(), market)


def normalize_code(value: str) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not (1 <= len(digits) <= 6):
        raise ValueError(f"股票代码格式不正确：{value}")
    return digits.zfill(6)


def normalize_codes_arg(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        value = ",".join(value)
    codes = []
    for item in re.split(r"[\s,;]+", value):
        item = item.strip()
        if item:
            codes.append(normalize_code(item))
    return codes


def stock_payload(record: StockRecord) -> dict[str, Any]:
    return {
        "code": record.code,
        "full_code": record.full_code,
        "market": record.market.lower(),
        "market_label": market_label(record.market),
        "name": record.name,
        "extended_name": record.name,
        "asset_type": "stock",
        "type_label": "股票",
        "label": f"{record.name} {record.code}",
        "title": f"{record.name} {record.code}",
    }


def is_a_share_stock_record(item: dict[str, Any]) -> bool:
    code = str(item.get("code", "")).zfill(6)
    full_code = str(item.get("full_code") or "").lower()
    market = str(item.get("market") or "").lower()
    name_text = " ".join([
        str(item.get("name") or ""),
        str(item.get("extended_name") or ""),
        str(item.get("english_name") or ""),
    ]).lower()
    if market not in {"sh", "sz"} and not full_code.startswith(("sh.", "sz.")):
        return False
    if not code.startswith(("0", "3", "6")):
        return False
    if "etf" in name_text or "基金" in name_text:
        return False
    if code.startswith(("200", "900")):
        return False
    return True


def load_dataset_stocks() -> list[StockRecord]:
    path = ROOT_DIR / "Dataset" / "stocks.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到股票列表文件：{path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for item in records:
        code = str(item.get("code", "")).zfill(6)
        name = str(item.get("name") or item.get("extended_name") or code)
        if not re.fullmatch(r"\d{6}", code) or not is_a_share_stock_record(item):
            continue
        market = str(item.get("market") or market_from_code(code)).upper()
        result.setdefault(code, StockRecord(code=code, name=name, market=market))
    return list(result.values())


def load_dataset_stock_names() -> dict[str, str]:
    try:
        return {record.code: record.name for record in load_dataset_stocks()}
    except Exception:
        return {}


def select_scan_stocks(codes: list[str], limit: int | None) -> list[StockRecord]:
    if codes:
        names = load_dataset_stock_names()
        return [
            StockRecord(code=code, name=names.get(code, code), market=market_from_code(code))
            for code in codes[:limit]
        ]

    records = load_dataset_stocks()
    if limit is not None:
        records = records[:limit]
    return records


def reset_output_dir(output_dir: Path, overwrite: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stocks_dir = output_dir / "stocks"
    if overwrite:
        for filename in ("latest_scan.json", "errors.json"):
            path = output_dir / filename
            if path.exists():
                path.unlink()
        if stocks_dir.exists():
            shutil.rmtree(stocks_dir)
    stocks_dir.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_kline_date(value: Any) -> str:
    text = str(value or "")
    date_part = text.split(" ", 1)[0].replace("/", "-")
    try:
        return datetime.strptime(date_part, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return date_part


def format_signal_type(type_value: str, is_buy: bool, is_seg: bool) -> str:
    direction = "买" if is_buy else "卖"
    type_names = {
        "1": "一",
        "1p": "盘背一",
        "2": "二",
        "2s": "类二",
        "3a": "三A",
        "3b": "三B",
    }
    parts = []
    for raw_type in str(type_value or "").split(","):
        raw_type = raw_type.strip()
        if not raw_type:
            continue
        parts.append(f"{type_names.get(raw_type, raw_type)}{direction}")
    label = "、".join(parts) if parts else direction
    return f"线段{label}" if is_seg else label


def collect_recent_signals(payload: dict[str, Any], recent_count: int) -> list[dict[str, Any]]:
    candles = payload.get("candles") or []
    if not candles:
        return []

    recent_start_idx = max(0, len(candles) - recent_count)
    candles_by_idx = {int(candle["idx"]): candle for candle in candles}
    signals: list[dict[str, Any]] = []
    for source_key in ("bsp", "seg_bsp"):
        for item in payload.get("elements", {}).get(source_key, []) or []:
            idx = int(item["x"])
            if idx < recent_start_idx:
                continue
            candle = candles_by_idx.get(idx) or (candles[idx] if 0 <= idx < len(candles) else {})
            is_buy = bool(item["is_buy"])
            signals.append({
                "date": normalize_kline_date(candle.get("time")),
                "type": item.get("type"),
                "label": item.get("label"),
                "display_type": format_signal_type(item.get("type", ""), is_buy, bool(item.get("is_seg"))),
                "direction": "buy" if is_buy else "sell",
                "is_seg": bool(item.get("is_seg")),
                "source": source_key,
                "x": idx,
                "price": safe_float(item.get("y")),
                "close": safe_float(candle.get("close")),
            })

    signals.sort(key=lambda item: (item["date"], item["x"], item["display_type"]))
    return signals


def build_detail_payload(
    payload: dict[str, Any],
    record: StockRecord,
    signals: list[dict[str, Any]],
    recent_count: int,
) -> dict[str, Any]:
    detail = dict(payload)
    detail.update({
        "market": record.market,
        "full_code": record.full_code,
        "scan_signals": signals,
        "recent_k_count": recent_count,
        "kline": payload.get("candles", []),
        "chan": {
            "bi": payload.get("elements", {}).get("bi", []),
            "seg": payload.get("elements", {}).get("seg", []),
            "zs": payload.get("elements", {}).get("zs", []),
            "seg_zs": payload.get("elements", {}).get("seg_zs", []),
            "bs_points": payload.get("elements", {}).get("bsp", []),
            "seg_bs_points": payload.get("elements", {}).get("seg_bsp", []),
        },
    })
    return detail


def analyze_stock(
    record: StockRecord,
    start_date: str,
    end_date: str,
    recent_count: int,
    retries: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if record.market.upper() == "BJ":
        raise ValueError("Baostock 当前不支持北交所股票 K 线，已跳过")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            chan = CChan(
                code=record.full_code,
                begin_time=start_date,
                end_time=end_date,
                data_src=DATA_SRC.BAO_STOCK,
                lv_list=[KL_TYPE.K_DAY],
                config=build_config(),
                autype=AUTYPE.QFQ,
            )
            payload = build_chart_payload_from_chan(
                chan=chan,
                code=record.full_code,
                level="day",
                start=start_date,
                end=end_date,
                stock_payload=stock_payload(record),
            )
            signals = collect_recent_signals(payload, recent_count)
            return payload, signals
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    assert last_error is not None
    raise last_error


def stock_summary(
    record: StockRecord,
    payload: dict[str, Any],
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    candles = payload.get("candles") or []
    latest = candles[-1] if candles else {}
    latest_close = safe_float(latest.get("close"))
    return {
        "code": record.code,
        "name": record.name,
        "market": record.market,
        "full_code": record.full_code,
        "latest_date": normalize_kline_date(latest.get("time")),
        "latest_close": latest_close,
        "signals": signals,
        "signal_count": len(signals),
        "data_file": f"stocks/{record.code}.json",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扫描中国 A 股最近日 K 缠论买卖点")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="K 线起始日期，默认 2024-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="K 线结束日期，默认今天")
    parser.add_argument("--recent-count", type=int, default=DEFAULT_RECENT_COUNT, help="最近 N 根有效日 K 判断窗口，默认 7")
    parser.add_argument("--limit", type=int, default=None, help="只扫描前 N 只股票，用于小样本测试")
    parser.add_argument("--codes", nargs="+", default=None, help="指定股票代码，逗号或空格分隔，例如 600000,000001")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="扫描结果输出目录")
    parser.add_argument("--overwrite", action="store_true", default=True, help="覆盖旧扫描结果，默认开启")
    parser.add_argument("--no-overwrite", action="store_false", dest="overwrite", help="不清空旧明细文件")
    parser.add_argument("--retries", type=int, default=3, help="单只股票失败重试次数，默认 3")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.recent_count <= 0:
        raise ValueError("--recent-count 必须大于 0")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    if args.start_date > args.end_date:
        raise ValueError("--start-date 不能晚于 --end-date")

    codes = normalize_codes_arg(args.codes)
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = ROOT_DIR / output_dir
    output_dir = output_dir.resolve()
    reset_output_dir(output_dir, args.overwrite)

    stocks = select_scan_stocks(codes, args.limit)
    total = len(stocks)
    matched: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    success_count = 0

    started_at = datetime.now()
    progress = tqdm(stocks, total=total, unit="只", desc="A股日线扫描", dynamic_ncols=True)
    for record in progress:
        progress.set_postfix({
            "code": record.code,
            "hits": len(matched),
            "errors": len(errors),
        }, refresh=False)
        try:
            payload, signals = analyze_stock(
                record=record,
                start_date=args.start_date,
                end_date=args.end_date,
                recent_count=args.recent_count,
                retries=max(1, args.retries),
            )
            success_count += 1

            if signals:
                detail = build_detail_payload(payload, record, signals, args.recent_count)
                write_json(output_dir / "stocks" / f"{record.code}.json", detail)
                matched.append(stock_summary(record, payload, signals))
        except Exception as exc:
            errors.append({
                "code": record.code,
                "name": record.name,
                "market": record.market,
                "error": str(exc),
            })
        finally:
            progress.set_postfix({
                "code": record.code,
                "hits": len(matched),
                "errors": len(errors),
            }, refresh=True)

    latest_payload = {
        "scan_time": started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": "A_SHARE",
        "level": "daily",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "recent_k_count": args.recent_count,
        "total_scanned": total,
        "success_count": success_count,
        "matched_count": len(matched),
        "error_count": len(errors),
        "stocks": matched,
        "error_file": "errors.json",
    }
    write_json(output_dir / "latest_scan.json", latest_payload)
    write_json(output_dir / "errors.json", {
        "scan_time": latest_payload["scan_time"],
        "finished_time": latest_payload["finished_time"],
        "error_count": len(errors),
        "errors": errors,
    })

    print()
    print("扫描完成")
    print(f"总股票数: {total}")
    print(f"成功扫描: {success_count}")
    print(f"命中股票: {len(matched)}")
    print(f"失败股票: {len(errors)}")
    print(f"结果保存: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
