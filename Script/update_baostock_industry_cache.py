from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import baostock as bs

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT_DIR / "Web" / "data" / "industry" / "stock_industry.json"


def normalize_code(code: str) -> str:
    text = str(code or "").strip().lower()
    return text.split(".", 1)[-1] if "." in text else text


def fetch_industry_rows(query_date: str = "", code: str = "") -> tuple[list[str], list[list[str]]]:
    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login_result.error_msg}")

    try:
        rs = bs.query_stock_industry(code=code, date=query_date)
        if rs.error_code != "0":
            raise RuntimeError(f"query_stock_industry 失败：{rs.error_msg}")

        rows: list[list[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
        return list(rs.fields), rows
    finally:
        bs.logout()


def build_cache_payload(fields: list[str], rows: list[list[str]], query_date: str, limit: int | None = None) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    update_dates = set()
    classifications = set()
    industries = set()

    selected_rows = rows[:limit] if limit is not None else rows
    for row in selected_rows:
        item = dict(zip(fields, row))
        full_code = str(item.get("code") or "").strip().lower()
        code = normalize_code(full_code)
        if not code:
            continue

        update_date = str(item.get("updateDate") or "").strip()
        industry = str(item.get("industry") or "").strip()
        industry_classification = str(item.get("industryClassification") or "").strip()
        if update_date:
            update_dates.add(update_date)
        if industry:
            industries.add(industry)
        if industry_classification:
            classifications.add(industry_classification)

        records[code] = {
            "code": code,
            "full_code": full_code,
            "name": str(item.get("code_name") or "").strip(),
            "industry": industry,
            "industry_classification": industry_classification,
            "industry_update_date": update_date,
        }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query_date": query_date,
        "source": "baostock.query_stock_industry",
        "record_count": len(records),
        "raw_record_count": len(rows),
        "update_dates": sorted(update_dates),
        "industry_classifications": sorted(classifications),
        "industries": sorted(industries),
        "stocks": records,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 baostock 股票行业分类本地缓存")
    parser.add_argument("--date", default="", help="查询日期，格式 YYYY-MM-DD；默认使用 baostock 最新行业数据")
    parser.add_argument("--code", default="", help="只更新指定证券代码，例如 sh.600000；默认获取全部")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    parser.add_argument("--limit", type=int, default=None, help="只保存前 N 条，用于脚本测试")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    if args.date:
        date.fromisoformat(args.date)

    fields, rows = fetch_industry_rows(query_date=args.date, code=args.code)
    payload = build_cache_payload(fields, rows, args.date, args.limit)

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT_DIR / output
    write_json(output.resolve(), payload)

    print("行业缓存已更新")
    print(f"原始记录数: {payload['raw_record_count']}")
    print(f"保存记录数: {payload['record_count']}")
    print(f"行业数量: {len(payload['industries'])}")
    print(f"更新日期: {', '.join(payload['update_dates']) or '-'}")
    print(f"保存路径: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
