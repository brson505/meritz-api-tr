"""메리츠증권 Open API — data.txt 를 읽어 지정가 주문 일괄 전송

  POST /trading/v1/orders/buy    (매수)
  POST /trading/v1/orders/sell   (매도)      TR obtrs_oder_101a, TPS 10

!! 운영 서버로 실제 주문이 나갑니다.
   기본 동작은 '미리보기'이며, --yes 를 붙이고 'yes' 를 입력해야 전송됩니다.
!! 같은 파일을 다시 실행하면 같은 주문이 '또' 나갑니다 (중복 방지 장치 없음).

data.txt 형식 (공백/탭 구분, 한 줄에 한 주문: 매수/매도  종목코드  가격  수량)
  buy     A122630 44870   1
  buy     A122630 42775   1
  sell    A122630 47855   1
  sell    A122630 49765   1
  # 으로 시작하는 줄과 빈 줄은 무시합니다. 종목코드는 122630 / A122630 모두 가능.

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿
  (meritz_cancel_order.py 와 같은 폴더에 두세요 — 인증/응답판정 코드를 가져다 씁니다)

사용 예
  python meritz_place_orders.py data.txt              # 검증 + 미리보기만 (전송 안 함)
  python meritz_place_orders.py data.txt --yes        # 확인 후 실제 전송
  python meritz_place_orders.py data.txt --yes --exch 02   # NXT 로 주문
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from meritz_cancel_order import (
    BASE_URL,
    TIMEOUT,
    MeritzError,
    MeritzWarning,
    _check,
    _mac_address,
    get_token,
)

SIDES = {"buy": "buy", "b": "buy", "매수": "buy", "sell": "sell", "s": "sell", "매도": "sell"}
SIDE_KO = {"buy": "매수", "sell": "매도"}


@dataclass(frozen=True)
class Order:
    line_no: int
    side: str   # "buy" | "sell"
    iscd: str   # "A" + 6자리
    price: int
    qty: int


# --------------------------------------------------------------------------- #
# 파일 파싱 / 검증
# --------------------------------------------------------------------------- #
def parse_file(path: Path) -> list[Order]:
    """data.txt 를 읽어 검증한다. 한 줄이라도 잘못되면 전부 보고하고 중단한다."""
    orders: list[Order] = []
    errors: list[str] = []

    # utf-8-sig: 메모장 등에서 저장한 BOM 을 안전하게 처리
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            errors.append(f"{no}행: 항목이 4개(매수/매도 코드 가격 수량)가 아닙니다 → {raw!r}")
            continue

        side_raw, code, price_raw, qty_raw = parts
        side = SIDES.get(side_raw.lower())
        code = code.upper()
        if len(code) == 6:
            code = "A" + code

        problems = []
        if side is None:
            problems.append(f"매수/매도 구분 '{side_raw}' (buy 또는 sell)")
        if not re.fullmatch(r"A\d{6}", code):
            problems.append(f"종목코드 '{parts[1]}' (6자리 숫자 또는 A+6자리)")
        if not re.fullmatch(r"\d+", price_raw.replace(",", "")) or int(price_raw.replace(",", "")) <= 0:
            problems.append(f"가격 '{price_raw}' (양의 정수)")
        if not re.fullmatch(r"\d+", qty_raw.replace(",", "")) or int(qty_raw.replace(",", "")) <= 0:
            problems.append(f"수량 '{qty_raw}' (양의 정수)")
        if problems:
            errors.append(f"{no}행: " + ", ".join(problems))
            continue

        orders.append(Order(no, side, code, int(price_raw.replace(",", "")), int(qty_raw.replace(",", ""))))

    if errors:
        raise ValueError("data 파일에 오류가 있어 아무것도 전송하지 않았습니다.\n  " + "\n  ".join(errors))
    if not orders:
        raise ValueError("주문할 내용이 없습니다. (빈 파일이거나 주석뿐입니다)")
    return orders


def print_preview(orders: list[Order], exch: str) -> None:
    exch_name = {"01": "KRX", "02": "NXT", "03": "SOR"}[exch]
    print(f"\n주문 {len(orders)}건 · 지정가 · 거래소 {exch_name}")
    print("-" * 60)
    print(f"{'행':>3}  {'구분':<4} {'종목':<9} {'가격':>12} {'수량':>8} {'금액':>14}")
    print("-" * 60)
    totals = {"buy": 0, "sell": 0}
    for o in orders:
        amt = o.price * o.qty
        totals[o.side] += amt
        print(f"{o.line_no:>3}  {SIDE_KO[o.side]:<4} {o.iscd:<9} {o.price:>12,} {o.qty:>8,} {amt:>14,}")
    print("-" * 60)
    print(f"매수 합계 {totals['buy']:>14,}원\n매도 합계 {totals['sell']:>14,}원")


# --------------------------------------------------------------------------- #
# 주문 전송
# --------------------------------------------------------------------------- #
def place_order(o: Order, exch: str, *, warn_confirmed: bool = False) -> dict:
    """지정가(01) 일반 주문 1건 전송. 접수되면 응답 본문을 돌려준다."""
    payload: dict[str, Any] = {
        "iscd": o.iscd,
        "oder_unpr": o.price,
        "odqt": o.qty,
        "oder_cls_code": "01",       # 보통(지정가)
        "oder_cond_cls_code": "0",   # 일반
        "orgl_oder_no": 0,           # 신규 주문
        "whol_rctf_cncl_yn": "N",
        "warn_cnfr_yn": "Y" if warn_confirmed else "N",
        "exch_kind_code": exch,
    }
    resp = requests.post(
        f"{BASE_URL}/trading/v1/orders/{o.side}",
        json=payload,
        headers={
            "authorization": f"Bearer {get_token()}",
            "mac_address": _mac_address(),
            "content-type": "application/json; charset=utf-8",
        },
        timeout=TIMEOUT,
    )
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    # 오류는 HTTP 500 + rsp_msg, 경고는 rsp_cd 가 0001 이어도 warn_cls_code != "0" 이면 미접수.
    return _check(resp.status_code, body)


def send_all(orders: list[Order], exch: str, stop_on_error: bool) -> list[dict]:
    results: list[dict] = []
    for o in orders:
        label = f"{o.line_no}행 {SIDE_KO[o.side]} {o.iscd} {o.price:,}원 × {o.qty:,}"
        row = {"line": o.line_no, "side": o.side, "iscd": o.iscd, "price": o.price,
               "qty": o.qty, "status": "", "oder_no": "", "message": ""}
        try:
            try:
                res = place_order(o, exch)
            except MeritzWarning as w:
                print(f"\n[경고] {label}\n  {w}")
                if not w.resendable:
                    row.update(status="보류(미접수)", message=str(w))
                    results.append(row)
                    print(f"  → 접수되지 않았습니다.")
                    continue
                if input("  내용을 확인했고 이 주문을 그대로 전송하시겠습니까? (y/N) ").strip().lower() != "y":
                    row.update(status="건너뜀", message="경고 확인 거절")
                    results.append(row)
                    continue
                res = place_order(o, exch, warn_confirmed=True)

            data = res.get("data") or {}
            row.update(status="접수", oder_no=data.get("oder_no", ""), message=res.get("rsp_msg", ""))
            print(f"[접수] {label} → 주문번호 {row['oder_no']}")

        except (requests.Timeout, requests.ConnectionError) as e:
            # 요청은 나갔는데 응답만 못 받았을 수 있다. 자동 재시도하면 중복 주문이 된다.
            row.update(status="미확인", message=f"응답 없음({type(e).__name__}) — 주문내역에서 접수 여부 확인 필요")
            print(f"[미확인] {label}\n  {row['message']}")
            results.append(row)
            print("\n응답을 받지 못해 이후 주문을 중단합니다. 주문내역을 확인한 뒤 남은 주문을 다시 진행하세요.")
            break
        except (MeritzError, ValueError) as e:
            row.update(status="실패", message=str(e))
            print(f"[실패] {label}\n  {e}")
            if stop_on_error:
                results.append(row)
                print("\n--stop-on-error 에 따라 이후 주문을 중단합니다.")
                break

        results.append(row)
        time.sleep(0.15)  # TPS 10 제한 여유
    return results


def save_log(results: list[dict], src: Path) -> Path:
    out = src.with_name(f"{src.stem}_result_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["line", "side", "iscd", "price", "qty", "status", "oder_no", "message"])
        w.writeheader()
        w.writerows(results)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="data.txt 로 메리츠증권 지정가 주문 일괄 전송")
    p.add_argument("file", type=Path, nargs="?", default=Path("data.txt"), help="주문 파일 (기본 data.txt)")
    p.add_argument("--exch", default="01", choices=["01", "02", "03"],
                   help="거래소 01=KRX, 02=NXT, 03=SOR (기본 01)")
    p.add_argument("--yes", action="store_true", help="실제로 전송 (없으면 미리보기만)")
    p.add_argument("--stop-on-error", action="store_true", help="주문 하나라도 실패하면 이후 주문 중단")
    a = p.parse_args()

    try:
        orders = parse_file(a.file)
    except (OSError, ValueError) as e:
        raise SystemExit(str(e)) from None

    print_preview(orders, a.exch)
    if not a.yes:
        print("\n[미리보기] 전송하지 않았습니다. 실제로 주문하려면 --yes 를 붙이세요.")
        return

    #if input(f"\n위 {len(orders)}건을 실제로 주문합니다. 진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
    #    print("주문하지 않고 종료합니다.")
    #    return

    results = send_all(orders, a.exch, a.stop_on_error)

    ok = sum(r["status"] == "접수" for r in results)
    print(f"\n완료: 접수 {ok}건 / 전체 {len(orders)}건 (처리 {len(results)}건)")
    bad = [r for r in results if r["status"] not in ("접수",)]
    if bad:
        print("접수되지 않은 주문:", ", ".join(f"{r['line']}행({r['status']})" for r in bad))
    print("로그 저장:", save_log(results, a.file))
    print("※ 접수는 체결이 아닙니다. 체결·미체결은 주문내역에서 확인하세요.")


if __name__ == "__main__":
    sys.exit(main())
