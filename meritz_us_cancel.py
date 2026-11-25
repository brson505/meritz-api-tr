"""메리츠증권 Open API — 미국 주식 미체결 주문 조회 → 선택 → 취소

  1) GET  /trading/v1/overseas/orders/history  (TR obtro_oder_201r)  미체결 주문 찾기
  2) POST /trading/v1/overseas/orders/cancel   (TR obtro_oder_102a)  선택한 주문 취소

!! 같은 폴더에 meritz_cancel_order.py, meritz_us_orders.py 가 있어야 합니다.
!! --cancel 을 붙여도 번호 선택과 'yes' 입력을 거쳐야 실제로 취소됩니다. (운영 서버)

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿

사용 예
  python meritz_us_cancel.py                     # 최근 3일 미국 미체결 주문 목록만 출력
  python meritz_us_cancel.py --iscd AAPL         # 특정 종목만
  python meritz_us_cancel.py --side buy          # 매수만 (sell / all)
  python meritz_us_cancel.py --days 7            # 조회 기간 (오늘 포함 N일 전부터)
  python meritz_us_cancel.py --cancel            # 목록에서 골라 전량 취소

해외 주문내역은 국내와 규칙이 다릅니다 (코드 주석 참고).
  - 정정·취소 행의 원주문번호는 '최초 주문'이 아니라 '바로 직전 건'을 가리킵니다.
  - 미체결 수량 칸(ncnt_odqt)은 믿을 수 없어 '주문수량 − 체결수량'으로 직접 계산합니다.
  - 취소에는 주문일자·원주문의 주문구분·주문수량이 필요합니다.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import requests

from meritz_cancel_order import (
    BASE_URL,
    TIMEOUT,
    MeritzError,
    MeritzWarning,
    _mac_address,
    get_token,
)
from meritz_us_orders import _check  # 해외 주문용 응답 판정 (경고 1·3 만 재전송 가능)

MAX_PAGES = 50  # 연속조회 무한 루프 방지

# 주문내역의 호가유형(aspr_type_code) → 취소 요청의 주문구분(oder_cls_code)
ASPR_TO_CLS = {"1": "05", "2": "01", "4": "31", "5": "32", "6": "33", "7": "34",
               "8": "35", "9": "36", "A": "37", "B": "38"}
ASPR_NAME = {"1": "시장가", "2": "지정가", "4": "LOO", "5": "LOC", "6": "MOO", "7": "MOC",
             "8": "LimitVWAP", "9": "LimitTWAP", "A": "VWAP", "B": "TWAP"}


# --------------------------------------------------------------------------- #
# 주문내역 조회
# --------------------------------------------------------------------------- #
def _history_page(params: dict[str, str]) -> dict:
    resp = requests.get(
        f"{BASE_URL}/trading/v1/overseas/orders/history",
        params=params,
        headers={"authorization": f"Bearer {get_token()}", "mac_address": _mac_address()},
        timeout=TIMEOUT,
    )
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text

    # 오류는 HTTP 500 으로 오고, 오류 본문에는 data·tr_cont 가 없다 — 먼저 상태부터 본다.
    if resp.status_code != 200 or not isinstance(body, dict):
        detail = ""
        if isinstance(body, dict):
            detail = " ".join(str(body.get(k)) for k in ("rsp_cd", "rsp_msg", "rsp_sub_msg") if body.get(k))
        raise MeritzError(f"주문내역 조회 실패 (HTTP {resp.status_code}) {detail}".strip(),
                          status=resp.status_code, body=body)

    code = body.get("rsp_cd")
    if code in ("5762", "5766", "5820"):   # 더 있음 / 완료 / 자료 없음
        return body
    sub = body.get("rsp_sub_msg")
    raise MeritzError(f"업무 오류 {code} — {body.get('rsp_msg')}" + (f" — {sub}" if sub else ""),
                      status=resp.status_code, body=body)


def fetch_history(start: str, end: str, side: str = "0", cntg: str = "2") -> list[dict]:
    """기간 내 주문내역을 연속조회로 모두 가져온다. 기본은 미체결(cntg_cls_code=2).

    side: "0" 전체 / "1" 매도 / "2" 매수  (이 세 값만 — 다른 값은 '자료 없음'처럼 보인다)
    cntg: "0" 전체 / "1" 체결 / "2" 미체결
    """
    if side not in ("0", "1", "2"):
        raise ValueError("side 는 0 / 1 / 2 중 하나여야 합니다.")
    if cntg not in ("0", "1", "2"):
        raise ValueError("cntg 는 0 / 1 / 2 중 하나여야 합니다.")
    params: dict[str, str] = {
        "oder_sta_date": start,
        "oder_fin_date": end,   # "00000000" 은 넣지 말 것 (3174 오류)
        "seln_byov_clcd": side,
        "cntg_cls_code": cntg,
    }
    rows: list[dict] = []
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        body = _history_page(params)
        rows.extend(body.get("data") or [])

        if body.get("rsp_cd") != "5762" or body.get("tr_cont") != "1":
            break
        key = body.get("tr_cont_key")
        if not key or key in seen:   # 같은 페이지를 반복해 받는 것을 막는다
            break
        seen.add(key)
        params["tr_cont"] = "1"
        params["tr_cont_key"] = key   # 한 글자도 고치지 않고 그대로
    else:
        print(f"[주의] {MAX_PAGES}페이지에서 조회를 멈췄습니다. 기간·조건을 좁혀 다시 조회하세요.")
    return rows


def _symbol(iscd: str) -> str:
    """'PLTR.OQ' → 'PLTR' (거래소 접미어 제거. BRK.B.NY 처럼 점이 더 있어도 마지막만 뗀다)."""
    return iscd.rsplit(".", 1)[0] if "." in iscd else iscd


def remaining_qty(r: dict) -> Decimal:
    """남은 수량 = 주문수량 − 체결수량. (ncnt_odqt 는 실제와 다를 수 있어 쓰지 않는다)"""
    return Decimal(str(r.get("oder_dqty") or "0")) - Decimal(str(r.get("cntg_dqty") or "0"))


def find_open_orders(rows: list[dict], *, us_only: bool = True, iscd: str | None = None) -> list[dict]:
    """취소 가능한 '살아 있는' 주문만 추린다.

    해외 주문내역에서 정정·취소 건은 원주문과 별개의 행이고, 그 행의 orgl_oder_no 는
    '바로 직전 건'의 번호다. 그래서 정상 접수된 정정·취소 행이 가리키는 주문은
    (이미 정정·취소로 넘어갔으므로) 취소 대상에서 뺀다.
    주문번호만으로는 종목이 달라도 겹칠 수 있어 (주문일자, 주문번호, 종목)으로 구분한다.
    """
    superseded: set[tuple[str, int, str]] = set()
    for r in rows:
        if str(r.get("oder_rctf_cncl_cls_code")) in ("1", "2") and str(r.get("ovrs_stck_oder_stat_cls_code")) == "0":
            prev = int(r.get("orgl_oder_no") or 0)
            if prev:
                superseded.add((r["oder_date"], prev, r["iscd"]))

    out: list[dict] = []
    for r in rows:
        if str(r.get("oder_rctf_cncl_cls_code")) == "2":          # 취소 행 자체
            continue
        if str(r.get("ovrs_stck_oder_stat_cls_code")) != "0":      # 정상 접수만 (거부·미접수 제외)
            continue
        if remaining_qty(r) <= 0:
            continue
        if (r["oder_date"], int(r["oder_no"]), r["iscd"]) in superseded:
            continue
        if us_only and str(r.get("ovrs_stck_mrkt_tr_cls_code")) != "01":
            continue
        if iscd and _symbol(r["iscd"]).upper() != iscd.upper():
            continue
        out.append(r)
    return sorted(out, key=lambda r: (r["oder_date"], int(r["oder_no"])))


# --------------------------------------------------------------------------- #
# 출력 / 선택
# --------------------------------------------------------------------------- #
def _fmt_qty(d: Decimal) -> str:
    return format(d.normalize(), "f")


def print_orders(orders: list[dict]) -> None:
    print(f"\n미국 미체결 주문 {len(orders)}건")
    print("-" * 100)
    print(f"{'번호':>3} {'주문일자':<9} {'주문번호':>10} {'구분':<4} {'심볼':<8} {'유형':<9} "
          f"{'주문가':>12} {'주문량':>8} {'미체결':>8} {'접수시각':<19}")
    print("-" * 100)
    for i, r in enumerate(orders, 1):
        side = "매수" if str(r["seln_byov_clcd"]) == "2" else "매도"
        typ = ASPR_NAME.get(str(r["aspr_type_code"]), str(r["aspr_type_code"]))
        price = "시장가" if str(r["aspr_type_code"]) in ("1", "6", "7", "A", "B") else \
            format(Decimal(str(r["ovrs_stck_oder_prc"])).normalize(), "f")
        mark = "  (정정건)" if str(r.get("oder_rctf_cncl_cls_code")) == "1" else ""
        print(f"{i:>3} {r['oder_date']:<9} {int(r['oder_no']):>10} {side:<4} {_symbol(r['iscd']):<8} {typ:<9} "
              f"{price:>12} {_fmt_qty(Decimal(str(r['oder_dqty']))):>8} {_fmt_qty(remaining_qty(r)):>8} "
              f"{r['oder_exec_ymdh']:<19}{mark}")
    print("-" * 100)


def parse_selection(text: str, n: int) -> list[int]:
    """'1,3' / '2-4' / 'all' → 0-based 인덱스 목록."""
    text = text.strip().lower()
    if text in ("all", "a", "전체"):
        return list(range(n))
    picked: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            picked.update(range(int(a), int(b) + 1))
        else:
            picked.add(int(part))
    bad = [p for p in picked if not 1 <= p <= n]
    if bad:
        raise ValueError(f"목록에 없는 번호입니다: {sorted(bad)}")
    return [p - 1 for p in sorted(picked)]


# --------------------------------------------------------------------------- #
# 취소 실행
# --------------------------------------------------------------------------- #
def cancel_us_order(r: dict, *, warn_confirmed: bool = False, confirm: bool = False) -> dict:
    """미체결 주문 1건 전량 취소. confirm=True 가 아니면 보낼 내용만 돌려준다(미리보기).

    odqt 는 '남은 수량'(주문수량 − 체결수량)으로 보낸다.
    """
    cls = ASPR_TO_CLS.get(str(r["aspr_type_code"]))
    if cls is None:
        raise ValueError(f"알 수 없는 호가유형 '{r['aspr_type_code']}' — 취소 요청의 주문구분을 정할 수 없습니다.")
    rem = remaining_qty(r)
    qty: int | float = int(rem) if rem == rem.to_integral_value() else float(rem)

    payload: dict[str, Any] = {
        "oder_date": r["oder_date"],
        "orgl_oder_no": int(r["oder_no"]),   # 정정했다면 그 정정 건의 번호 — 목록이 이미 그렇게 골라 준다
        "oder_cls_code": cls,                # 원주문의 주문구분 그대로
        "odqt": qty,
        "warn_cnfr_yn": "Y" if warn_confirmed else "N",
    }
    if not confirm:
        return {"needs_confirmation": True, "endpoint": "POST /trading/v1/overseas/orders/cancel",
                "will_send": payload}

    resp = requests.post(
        f"{BASE_URL}/trading/v1/overseas/orders/cancel",
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


def cancel_selected(targets: list[dict]) -> list[tuple[dict, str]]:
    results: list[tuple[dict, str]] = []
    for r in targets:
        name = f"{_symbol(r['iscd'])} 주문 {int(r['oder_no'])}"
        try:
            try:
                res = cancel_us_order(r, confirm=True)
            except MeritzWarning as w:
                print(f"\n[경고] {name}: {w}")
                if not w.resendable:
                    results.append((r, "보류 — 접수 안 됨"))
                    continue
                if input("  내용을 확인했고 계속 진행하시겠습니까? (y/N) ").strip().lower() != "y":
                    results.append((r, "사용자가 중단"))
                    continue
                res = cancel_us_order(r, warn_confirmed=True, confirm=True)
            results.append((r, f"취소 접수 (취소주문번호 {(res.get('data') or {}).get('oder_no')})"))
        except (requests.Timeout, requests.ConnectionError) as e:
            # 요청은 나갔는데 응답만 못 받았을 수 있다. 자동 재시도하지 않고 멈춘다.
            results.append((r, f"미확인 — 응답 없음({type(e).__name__}), 주문내역에서 확인 필요"))
            print(f"[미확인] {name}\n응답을 받지 못해 이후 취소를 중단합니다.")
            break
        except (MeritzError, ValueError) as e:
            results.append((r, f"실패: {e}"))
        time.sleep(0.15)  # TPS 10 제한 여유
    return results


def main() -> None:
    p = argparse.ArgumentParser(description="메리츠증권 미국 주식 미체결 주문 조회/취소")
    p.add_argument("--days", type=int, default=3, help="오늘 포함 며칠 전부터 조회할지 (기본 3)")
    p.add_argument("--iscd", help="심볼로 거르기 (예: AAPL)")
    p.add_argument("--side", choices=["all", "buy", "sell"], default="all", help="매매구분 (기본 all)")
    p.add_argument("--all-markets", action="store_true", help="미국 외 시장의 주문도 포함")
    p.add_argument("--cancel", action="store_true", help="목록에서 골라 전량 취소 (미지정 시 조회만)")
    a = p.parse_args()

    if a.days < 1:
        raise SystemExit("--days 는 1 이상이어야 합니다.")
    today = datetime.now(ZoneInfo("Asia/Seoul"))
    start = (today - timedelta(days=a.days - 1)).strftime("%Y%m%d")
    end = "20991231"  # 기간 끝을 열어 둔다 ("00000000" 은 3174 오류)
    side = {"all": "0", "sell": "1", "buy": "2"}[a.side]

    def load() -> list[dict]:
        return find_open_orders(fetch_history(start, end, side),
                                us_only=not a.all_markets, iscd=a.iscd)

    try:
        orders = load()
    except MeritzError as e:
        raise SystemExit(f"실패: {e}") from None

    if not orders:
        print(f"{start} 이후 미체결 주문이 없습니다.")
        return
    print_orders(orders)

    if not a.cancel:
        print("취소하려면 --cancel 옵션을 붙여 다시 실행하세요.")
        return

    try:
        raw = input("취소할 번호를 입력하세요 (예: 1,3 / 2-4 / all, 엔터=종료): ")
        if not raw.strip():
            print("종료합니다.")
            return
        targets = [orders[i] for i in parse_selection(raw, len(orders))]
    except ValueError as e:
        raise SystemExit(f"입력 오류: {e}") from None

    print(f"\n다음 {len(targets)}건을 전량 취소합니다:")
    for r in targets:
        side_txt = "매수" if str(r["seln_byov_clcd"]) == "2" else "매도"
        print(f"  - {r['oder_date']} 주문 {int(r['oder_no'])} · {_symbol(r['iscd'])} {side_txt} "
              f"{ASPR_NAME.get(str(r['aspr_type_code']))} 미체결 {_fmt_qty(remaining_qty(r))}주")
    if input("진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
        print("취소하지 않고 종료합니다.")
        return

    results = cancel_selected(targets)
    print("\n결과")
    for r, msg in results:
        print(f"  {_symbol(r['iscd'])} 주문 {int(r['oder_no'])}: {msg}")

    # 접수 성공 ≠ 취소 확정 — 다시 조회해서 아직 남아 있는지 확인한다.
    try:
        time.sleep(1)
        still = {(r["oder_date"], int(r["oder_no"]), r["iscd"]) for r in load()}
        left = [int(r["oder_no"]) for r, _ in results
                if (r["oder_date"], int(r["oder_no"]), r["iscd"]) in still]
        if left:
            print(f"\n[확인] 아직 미체결로 남아 있는 주문: {left}  (반영 지연이거나 취소 실패일 수 있습니다)")
        else:
            print("\n[확인] 선택한 주문이 미체결 목록에서 모두 사라졌습니다.")
    except MeritzError as e:
        print(f"\n[확인 생략] 재조회 실패: {e}")


if __name__ == "__main__":
    main()
