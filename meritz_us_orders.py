"""메리츠증권 Open API — 미국 주식 매수/매도 주문

  POST /trading/v1/overseas/orders/buy    (매수, TR obtro_oder_101a)
  POST /trading/v1/overseas/orders/sell   (매도, TR obtro_oder_101a)   TPS 10

!! 운영 서버로 실제 주문이 나갑니다 (외화 주문·환전이 발생할 수 있음).
   기본 동작은 '미리보기'이며, --yes 를 붙이고 'yes' 를 입력해야 전송됩니다.
!! 같은 주문을 다시 실행하면 같은 주문이 '또' 나갑니다 (중복 방지 장치 없음).

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿
  (meritz_cancel_order.py 와 같은 폴더에 두세요 — 인증 코드를 가져다 씁니다)

── 1) 주문 1건 ─────────────────────────────────────────────────────────────
  python meritz_us_orders.py buy  AAPL 150.25 1 --exch NASDAQ            # 미리보기
  python meritz_us_orders.py buy  AAPL 150.25 1 --exch NASDAQ --yes      # 실제 전송
  python meritz_us_orders.py sell TSLA 0 1 --exch NASDAQ --type market --yes   # 시장가 매도

── 2) 파일로 여러 건 ───────────────────────────────────────────────────────
  python meritz_us_orders.py --file us_orders.txt --exch NASDAQ --yes

  파일 형식 (공백/탭 구분): 매수/매도  심볼  가격  수량  [거래소]  [유형]
    buy   AAPL  150.25  1
    buy   MSFT  410.5   2  NASDAQ
    sell  KO    62.10   3  NYSE   limit
    sell  TSLA  0       1  -      market      # '-' 는 기본값 사용
  # 으로 시작하는 줄과 빈 줄은 무시합니다.

거래소: NASDAQ(0537) / NYSE(0321) / AMEX(0066)  — 종목이 실제 상장된 거래소여야 합니다.
        거래소는 줄마다 쓰거나 --exch 로 지정해야 하며, 기본값은 없습니다.
유형  : limit(지정가, 기본) / market(시장가) / loo / loc / moo / moc
        시장가 계열(market, moo, moc)은 가격 칸에 0 (또는 market)을 쓰세요.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

from meritz_cancel_order import (
    BASE_URL,
    TIMEOUT,
    MeritzError,
    MeritzWarning,
    _mac_address,
    get_token,
)

# 거래소: 입력 별칭 → 거래소 코드
EXCHANGES = {
    "NASDAQ": "0537", "NAS": "0537",
    "NYSE": "0321", "NYS": "0321",
    "AMEX": "0066", "AMS": "0066",
}
EXCH_NAME = {"0537": "NASDAQ", "0321": "NYSE", "0066": "AMEX"}

# 유형: 이름 → (oder_cls_code, oder_cond_cls_code, 가격 필요 여부)
# oder_cond_cls_code 는 생략하면 비어 나가므로 항상 oder_cls_code 에 맞춰 넣는다.
ORDER_TYPES = {
    "limit":  ("01", "0", True),
    "market": ("05", "0", False),
    "loo":    ("31", "2", True),    # 장 개시 지정가
    "loc":    ("32", "7", True),    # 장 마감 지정가
    "moo":    ("33", "2", False),   # 장 개시 시장가
    "moc":    ("34", "7", False),   # 장 마감 시장가
}

SIDES = {"buy": "buy", "b": "buy", "매수": "buy", "sell": "sell", "s": "sell", "매도": "sell"}
SIDE_KO = {"buy": "매수", "sell": "매도"}

# 해외 주문은 국내와 경고 코드 체계가 다르다: "1"(경고)·"3"(미국 PTP 과세 확인)만
# warn_cnfr_yn="Y" 재전송으로 접수되고, "2"(보류)를 포함한 나머지는 접수되지 않는다.
RESEND_OK = frozenset({"1", "3"})


@dataclass(frozen=True)
class Order:
    line_no: int
    side: str        # "buy" | "sell"
    symbol: str
    exch: str        # 거래소 코드 (0537 …)
    otype: str       # ORDER_TYPES 의 키
    price: Decimal   # 시장가 계열은 0
    qty: int

    @property
    def price_str(self) -> str:
        return format(self.price.normalize(), "f")


# --------------------------------------------------------------------------- #
# 입력 파싱 / 검증
# --------------------------------------------------------------------------- #
def parse_order(tokens: list[str], line_no: int, default_exch: str | None, default_type: str) -> Order:
    """토큰: 매수/매도 심볼 가격 수량 [거래소] [유형]. 잘못되면 ValueError."""
    if not 4 <= len(tokens) <= 6:
        raise ValueError("항목이 4~6개(매수/매도 심볼 가격 수량 [거래소] [유형])가 아닙니다")

    side_raw, sym_raw, price_raw, qty_raw = tokens[:4]
    exch_raw = tokens[4] if len(tokens) >= 5 and tokens[4] != "-" else default_exch
    type_raw = tokens[5] if len(tokens) == 6 and tokens[5] != "-" else default_type

    problems: list[str] = []

    side = SIDES.get(side_raw.lower())
    if side is None:
        problems.append(f"매수/매도 구분 '{side_raw}' (buy 또는 sell)")

    symbol = sym_raw.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,10}", symbol):
        problems.append(f"심볼 '{sym_raw}'")

    exch = EXCHANGES.get((exch_raw or "").upper())
    if exch is None:
        problems.append(f"거래소 '{exch_raw or '(미지정)'}' (NASDAQ / NYSE / AMEX 중 하나, --exch 또는 줄에 지정)")

    otype = (type_raw or "").lower()
    if otype not in ORDER_TYPES:
        problems.append(f"주문 유형 '{type_raw}' ({' / '.join(ORDER_TYPES)})")

    needs_price = ORDER_TYPES[otype][2] if otype in ORDER_TYPES else True
    price = Decimal(0)
    p = price_raw.replace(",", "").lower()
    if otype in ORDER_TYPES:
        if needs_price:
            if not re.fullmatch(r"\d+(\.\d+)?", p) or Decimal(p) <= 0:
                problems.append(f"가격 '{price_raw}' (0보다 큰 숫자)")
            else:
                price = Decimal(p)
                if price.normalize().as_tuple().exponent < -4:  # type: ignore[operator]
                    problems.append(f"가격 '{price_raw}' (소수점 이하 4자리까지, 초과하면 IGW50002 거절)")
        elif p not in ("0", "market"):
            problems.append(f"'{otype}' 주문은 가격 칸에 0 (또는 market)을 써야 합니다 (받은 값: '{price_raw}')")

    if not re.fullmatch(r"\d+", qty_raw.replace(",", "")) or int(qty_raw.replace(",", "")) <= 0:
        problems.append(f"수량 '{qty_raw}' (양의 정수)")

    if problems:
        raise ValueError(", ".join(problems))

    return Order(line_no, side, symbol, exch, otype, price, int(qty_raw.replace(",", "")))  # type: ignore[arg-type]


def parse_file(path: Path, default_exch: str | None, default_type: str) -> list[Order]:
    """파일 전체를 검증한다. 한 줄이라도 잘못되면 전부 보고하고 중단한다."""
    orders: list[Order] = []
    errors: list[str] = []
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()   # 줄 끝 주석 허용
        if not line:
            continue
        try:
            orders.append(parse_order(line.split(), no, default_exch, default_type))
        except ValueError as e:
            errors.append(f"{no}행: {e} → {raw.strip()!r}")
    if errors:
        raise ValueError("주문 파일에 오류가 있어 아무것도 전송하지 않았습니다.\n  " + "\n  ".join(errors))
    if not orders:
        raise ValueError("주문할 내용이 없습니다. (빈 파일이거나 주석뿐입니다)")
    return orders


def print_preview(orders: list[Order]) -> None:
    print(f"\n미국 주식 주문 {len(orders)}건")
    print("-" * 82)
    print(f"{'행':>3}  {'구분':<4} {'심볼':<8} {'거래소':<7} {'유형':<7} {'가격(USD)':>12} {'수량':>7} {'금액(USD)':>14}")
    print("-" * 82)
    totals = {"buy": Decimal(0), "sell": Decimal(0)}
    unpriced = 0
    for o in orders:
        if ORDER_TYPES[o.otype][2]:
            amt = o.price * o.qty
            totals[o.side] += amt
            price_txt, amt_txt = f"{o.price_str}", f"{amt:,.2f}"
        else:
            unpriced += 1
            price_txt, amt_txt = "시장가", "(미정)"
        print(f"{o.line_no:>3}  {SIDE_KO[o.side]:<4} {o.symbol:<8} {EXCH_NAME[o.exch]:<7} "
              f"{o.otype:<7} {price_txt:>12} {o.qty:>7,} {amt_txt:>14}")
    print("-" * 82)
    print(f"매수 합계 ${totals['buy']:>14,.2f}\n매도 합계 ${totals['sell']:>14,.2f}")
    if unpriced:
        print(f"※ 가격이 없는 주문(시장가 계열) {unpriced}건은 합계에 포함되지 않았습니다.")


# --------------------------------------------------------------------------- #
# 주문 전송
# --------------------------------------------------------------------------- #
def _check(status: int, body: Any) -> dict:
    """응답 판정. HTTP 200 / rsp_cd 만으로 접수를 단정하지 않는다."""
    # 오류는 종류와 무관하게 HTTP 500 으로 내려오고 사유는 rsp_msg 에 담긴다.
    if status != 200 or not isinstance(body, dict):
        e = body if isinstance(body, dict) else {}
        detail = " ".join(str(e.get(k)) for k in ("rsp_cd", "rsp_msg", "rsp_sub_msg") if e.get(k))
        raise MeritzError(f"HTTP {status}" + (f": {detail}" if detail else ""), status=status, body=body)

    # 경고 확인이 먼저: rsp_cd 가 0001 이어도 warn_cls_code 가 "0" 이 아니면 미접수.
    data = body.get("data") or {}
    warn = str(data.get("warn_cls_code", "0")).strip()
    if warn and warn != "0":
        ok = warn in RESEND_OK
        msg = str(data.get("warn_msg") or "").strip() or "확인이 필요한 주문입니다"
        raise MeritzWarning(
            msg + (' — warn_cnfr_yn="Y" 로 재전송하면 접수됩니다.' if ok
                   else " — 보류 상태라 재전송해도 접수되지 않습니다."),
            code=warn, resendable=ok, body=body,
        )

    code = body.get("rsp_cd")
    if code in ("0000", "0001"):
        return body
    sub = body.get("rsp_sub_msg")
    raise MeritzError(f"업무 오류 {code} — {body.get('rsp_msg')}" + (f" — {sub}" if sub else ""),
                      status=status, body=body)


def place_order(o: Order, *, warn_confirmed: bool = False) -> dict:
    cls_code, cond_code, _ = ORDER_TYPES[o.otype]
    payload: dict[str, Any] = {
        "ovrs_stck_symb_iscd": o.symbol,
        "ovrs_exch_vndr_idno": o.exch,
        "oder_cls_code": cls_code,
        "oder_cond_cls_code": cond_code,   # 항상 명시 (생략하면 빈 칸으로 나감)
        "odqt": o.qty,
        "ovrs_stck_oder_prc": o.price_str,  # 현지 통화(USD), 문자열
        "warn_cnfr_yn": "Y" if warn_confirmed else "N",
    }
    resp = requests.post(
        f"{BASE_URL}/trading/v1/overseas/orders/{o.side}",
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
    return _check(resp.status_code, body)


def send_all(orders: list[Order], stop_on_error: bool) -> list[dict]:
    results: list[dict] = []
    for o in orders:
        px = "시장가" if not ORDER_TYPES[o.otype][2] else f"${o.price_str}"
        label = f"{o.line_no}행 {SIDE_KO[o.side]} {o.symbol}({EXCH_NAME[o.exch]}) {o.otype} {px} × {o.qty:,}"
        row = {"line": o.line_no, "side": o.side, "symbol": o.symbol, "exchange": EXCH_NAME[o.exch],
               "type": o.otype, "price": o.price_str, "qty": o.qty,
               "status": "", "oder_no": "", "message": ""}
        try:
            try:
                res = place_order(o)
            except MeritzWarning as w:
                print(f"\n[경고] {label}\n  {w}")
                if not w.resendable:
                    row.update(status="보류(미접수)", message=str(w))
                    results.append(row)
                    print("  → 접수되지 않았습니다.")
                    continue
                if input("  내용을 확인했고 이 주문을 그대로 전송하시겠습니까? (y/N) ").strip().lower() != "y":
                    row.update(status="건너뜀", message="경고 확인 거절")
                    results.append(row)
                    continue
                res = place_order(o, warn_confirmed=True)

            data = res.get("data") or {}
            row.update(status="접수", oder_no=data.get("oder_no", ""), message=res.get("rsp_msg", ""))
            print(f"[접수] {label} → 주문번호 {row['oder_no']}")

        except (requests.Timeout, requests.ConnectionError) as e:
            # 요청은 나갔는데 응답만 못 받았을 수 있다. 자동 재시도하면 중복 주문이 된다.
            row.update(status="미확인", message=f"응답 없음({type(e).__name__}) — 주문내역에서 접수 여부 확인 필요")
            results.append(row)
            print(f"[미확인] {label}\n  {row['message']}")
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


def save_log(results: list[dict]) -> Path:
    out = Path(f"us_orders_result_{datetime.now():%Y%m%d_%H%M%S}.csv")
    fields = ["line", "side", "symbol", "exchange", "type", "price", "qty", "status", "oder_no", "message"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="메리츠증권 미국 주식 주문 (1건 또는 파일)",
        epilog="1건: 매수/매도 심볼 가격 수량  예) buy AAPL 150.25 1 --exch NASDAQ",
    )
    p.add_argument("order", nargs="*", help="매수/매도 심볼 가격 수량 (파일을 쓰면 생략)")
    p.add_argument("--file", type=Path, help="여러 건을 담은 주문 파일")
    p.add_argument("--exch", help="기본 거래소 NASDAQ / NYSE / AMEX (줄에 거래소가 없을 때 사용)")
    p.add_argument("--type", default="limit", choices=list(ORDER_TYPES), help="기본 주문 유형 (기본 limit)")
    p.add_argument("--yes", action="store_true", help="실제로 전송 (없으면 미리보기만)")
    p.add_argument("--stop-on-error", action="store_true", help="주문 하나라도 실패하면 이후 주문 중단")
    a = p.parse_args()

    if a.exch and a.exch.upper() not in EXCHANGES:
        raise SystemExit("--exch 는 NASDAQ / NYSE / AMEX 중 하나여야 합니다.")

    try:
        if a.file:
            if a.order:
                raise ValueError("--file 과 직접 입력은 함께 쓸 수 없습니다.")
            orders = parse_file(a.file, a.exch, a.type)
        elif a.order:
            orders = [parse_order(a.order, 1, a.exch, a.type)]
        else:
            p.error("주문 내용(매수/매도 심볼 가격 수량) 또는 --file 이 필요합니다.")
    except (OSError, ValueError) as e:
        raise SystemExit(str(e)) from None

    print_preview(orders)
    if not a.yes:
        print("\n[미리보기] 전송하지 않았습니다. 실제로 주문하려면 --yes 를 붙이세요.")
        return

    if input(f"\n위 {len(orders)}건을 실제로 주문합니다. 진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
        print("주문하지 않고 종료합니다.")
        return

    results = send_all(orders, a.stop_on_error)

    ok = sum(r["status"] == "접수" for r in results)
    print(f"\n완료: 접수 {ok}건 / 전체 {len(orders)}건 (처리 {len(results)}건)")
    bad = [r for r in results if r["status"] != "접수"]
    if bad:
        print("접수되지 않은 주문:", ", ".join(f"{r['line']}행({r['status']})" for r in bad))
    print("로그 저장:", save_log(results))
    print("※ 접수는 체결이 아닙니다. 체결·미체결은 주문내역에서 확인하세요.")


if __name__ == "__main__":
    sys.exit(main())
