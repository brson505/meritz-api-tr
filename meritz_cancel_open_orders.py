"""메리츠증권 Open API — 미체결 주문 조회 → 선택 → 취소

  1) GET  /trading/v1/orders/history  (TR obtrs_comn_615r)  미체결 주문 찾기
  2) POST /trading/v1/orders/cancel   (TR obtrs_oder_101a)  선택한 주문 취소

!! 같은 폴더에 meritz_cancel_order.py 가 있어야 합니다 (인증·취소 함수를 가져다 씁니다).
!! --cancel 을 붙여도 번호 선택과 'yes' 입력을 거쳐야 실제로 취소됩니다. (운영 서버)

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿

사용 예
  python meritz_cancel_open_orders.py                      # 오늘 미체결 주문 목록만 출력
  python meritz_cancel_open_orders.py --iscd 005930        # 특정 종목만
  python meritz_cancel_open_orders.py --side buy           # 매수 주문만 (sell / all)
  python meritz_cancel_open_orders.py --api-only           # Open API 로 낸 주문만
  python meritz_cancel_open_orders.py --date 20261124      # 날짜 지정 (하루 단위)
  python meritz_cancel_open_orders.py --cancel             # 목록에서 골라 취소
  python meritz_cancel_open_orders.py --cancel --exch 02   # NXT 주문이면 02 지정

거래소 코드 주의
  주문내역 응답에는 거래소 코드(exch_kind_code)가 없습니다.
  취소할 때는 주문을 낼 때 쓴 거래소(01 KRX / 02 NXT / 03 SOR)를 --exch 로 지정하세요.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

# 같은 폴더의 meritz_cancel_order.py 에서 인증/취소 로직을 재사용한다.
from meritz_cancel_order import (
    BASE_URL,
    TIMEOUT,
    MeritzError,
    MeritzWarning,
    _mac_address,
    cancel_order,
    get_token,
)

MAX_PAGES = 50  # 연속조회 무한 루프 방지


# --------------------------------------------------------------------------- #
# 주문내역 조회
# --------------------------------------------------------------------------- #
def _history_page(params: dict[str, str]) -> dict:
    resp = requests.get(
        f"{BASE_URL}/trading/v1/orders/history",
        params=params,
        headers={"authorization": f"Bearer {get_token()}", "mac_address": _mac_address()},
        timeout=TIMEOUT,
    )
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text

    # 오류는 종류와 무관하게 HTTP 500 으로 내려오고 사유는 rsp_msg 에 있다.
    if resp.status_code != 200 or not isinstance(body, dict):
        detail = ""
        if isinstance(body, dict):
            detail = " ".join(str(body.get(k)) for k in ("rsp_cd", "rsp_msg", "rsp_sub_msg") if body.get(k))
        raise MeritzError(f"주문내역 조회 실패 (HTTP {resp.status_code}) {detail}".strip(),
                          status=resp.status_code, body=body)

    code = body.get("rsp_cd")
    if code in ("5762", "5766", "5820", "5822", "0000"):
        return body
    sub = body.get("rsp_sub_msg")
    raise MeritzError(f"업무 오류 {code} — {body.get('rsp_msg')}" + (f" — {sub}" if sub else ""),
                      status=resp.status_code, body=body)


def fetch_orders(date: str, side: str = "0", iscd: str | None = None) -> list[dict]:
    """하루치 주문내역(남은 수량이 있는 주문 위주)을 연속조회로 모두 가져온다.

    side: "0" 전체 / "1" 매도 / "2" 매수
    """
    params: dict[str, str] = {
        "oder_date": date,
        "deal_cls_code": side,
        "cntg_cls_code": "2",   # 남은 수량이 있는 주문
        "sort_stnd_code": "2",  # 접수 순서
    }
    if iscd:
        params["iscd"] = iscd

    rows: list[dict] = []
    seen_keys: set[str] = set()
    for _ in range(MAX_PAGES):
        body = _history_page(params)
        rows.extend(body.get("data") or [])

        # 5762 + tr_cont "1" 일 때만 다음 페이지가 있다. (5766 / 5820 이면 끝)
        if body.get("rsp_cd") != "5762" or body.get("tr_cont") != "1":
            break
        key = body.get("tr_cont_key")
        # tr_cont_key 는 오른쪽에 공백이 붙어 온다 — 자르지 말고 그대로 보낸다.
        if not key or key in seen_keys:
            break  # 같은 페이지를 반복해 받는 것을 막는다
        seen_keys.add(key)
        params["tr_cont"] = "1"
        params["tr_cont_key"] = key
    else:
        print(f"[주의] {MAX_PAGES}페이지에서 조회를 멈췄습니다. 조건을 좁혀 다시 조회하세요.")
    return rows


def find_open_orders(rows: list[dict], api_only: bool = False) -> list[dict]:
    """취소 가능한 '살아 있는' 주문만 추린다.

    - 취소 행(rctf_cncl_cls_code=3), 거부(3)·미접수(0) 건은 제외
    - 미체결 수량(ncnt_qty)이 남은 건만
    - 정정을 거친 주문은 원주문·정정 행이 함께 와도 한 건으로 묶고,
      가장 최근 행(주문번호가 가장 큰 행)의 번호를 취소 대상으로 쓴다.
      (이미 정정한 주문은 '그 정정 건의 번호'로 취소해야 한다.)
    """
    latest: dict[int, dict] = {}
    for r in rows:
        if str(r.get("rctf_cncl_cls_code")) == "3":
            continue
        if str(r.get("oder_pros_stat_cls_code")) not in ("1", "2"):
            continue
        if int(r.get("ncnt_qty") or 0) <= 0:
            continue
        if api_only and str(r.get("chan_type_cls_code")) != "120":
            continue

        oder_no = int(r["oder_no"])
        root = int(r.get("orgl_oder_no") or 0) or oder_no  # 최초 주문 번호
        if root not in latest or oder_no > int(latest[root]["oder_no"]):
            latest[root] = r
    return sorted(latest.values(), key=lambda r: int(r["oder_no"]))


# --------------------------------------------------------------------------- #
# 출력 / 선택
# --------------------------------------------------------------------------- #
def _hhmmss(t: str) -> str:
    t = (t or "").ljust(6, "0")
    return f"{t[0:2]}:{t[2:4]}:{t[4:6]}"


def print_orders(orders: list[dict]) -> None:
    print(f"\n미체결 주문 {len(orders)}건")
    print("-" * 86)
    print(f"{'번호':>3} {'주문번호':>11} {'구분':<4} {'종목':<14} {'주문가':>10} {'주문량':>8} {'미체결':>8} {'접수시각':<8} 채널")
    print("-" * 86)
    channel = {"120": "API", "060": "HTS", "156": "MTS", "157": "MTS", "010": "영업점", "020": "홈피"}
    for i, r in enumerate(orders, 1):
        side = "매수" if str(r["deal_cls_code"]) == "2" else "매도"
        name = f"{r['kor_issu_abwr_name']}({r['iscd']})"[:20]
        warn = "" if r.get("oder_rfus_reas_code") in (None, "", "0000") else f"  ※거부사유코드 {r['oder_rfus_reas_code']}"
        print(f"{i:>3} {int(r['oder_no']):>11} {side:<4} {name:<14} {int(r['oder_unpr']):>10,} "
              f"{int(r['odqt']):>8,} {int(r['ncnt_qty']):>8,} {_hhmmss(r['oder_pros_sta_time']):<8} "
              f"{channel.get(str(r['chan_type_cls_code']), r['chan_type_cls_code'])}{warn}")
    print("-" * 86)


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
            lo, hi = int(a), int(b)
            picked.update(range(lo, hi + 1))
        else:
            picked.add(int(part))
    bad = [p for p in picked if not 1 <= p <= n]
    if bad:
        raise ValueError(f"목록에 없는 번호입니다: {sorted(bad)}")
    return [p - 1 for p in sorted(picked)]


# --------------------------------------------------------------------------- #
# 취소 실행
# --------------------------------------------------------------------------- #
def cancel_selected(targets: list[dict], exch: str) -> list[tuple[dict, str]]:
    """선택한 주문을 전량 취소. 각 주문의 (주문, 결과 문자열) 을 돌려준다."""
    results: list[tuple[dict, str]] = []
    for r in targets:
        no, iscd = int(r["oder_no"]), r["iscd"]
        try:
            try:
                res = cancel_order(iscd, no, whole=True, exch_kind_code=exch, confirm=True)
            except MeritzWarning as w:
                print(f"\n[경고] 주문 {no} ({r['kor_issu_abwr_name']}): {w}")
                if not w.resendable:
                    results.append((r, "보류 — 접수 안 됨"))
                    continue
                if input("  내용을 확인했고 계속 진행하시겠습니까? (y/N) ").strip().lower() != "y":
                    results.append((r, "사용자가 중단"))
                    continue
                res = cancel_order(iscd, no, whole=True, exch_kind_code=exch,
                                   warn_confirmed=True, confirm=True)
            new_no = (res.get("data") or {}).get("oder_no")
            results.append((r, f"취소 접수 (취소주문번호 {new_no})"))
        except (MeritzError, ValueError) as e:
            results.append((r, f"실패: {e}"))
        time.sleep(0.2)  # TPS 10 제한 여유
    return results


def main() -> None:
    p = argparse.ArgumentParser(description="메리츠증권 미체결 주문 조회/취소")
    p.add_argument("--date", help="주문일자 YYYYMMDD (기본: 오늘, 한국시간)")
    p.add_argument("--iscd", help="종목코드 (예: 005930)")
    p.add_argument("--side", choices=["all", "buy", "sell"], default="all", help="매매구분 (기본 all)")
    p.add_argument("--api-only", action="store_true", help="Open API(채널 120)로 낸 주문만")
    p.add_argument("--exch", default="01", choices=["01", "02", "03"],
                   help="취소 시 거래소 01=KRX, 02=NXT, 03=SOR (기본 01, 주문 때 쓴 값과 같아야 함)")
    p.add_argument("--cancel", action="store_true", help="목록에서 골라 취소 (미지정 시 조회만)")
    a = p.parse_args()

    date = a.date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    side = {"all": "0", "sell": "1", "buy": "2"}[a.side]

    try:
        rows = fetch_orders(date, side, a.iscd)
        orders = find_open_orders(rows, a.api_only)
    except MeritzError as e:
        raise SystemExit(f"실패: {e}") from None

    if not orders:
        print(f"{date} 미체결 주문이 없습니다.")
        return
    print_orders(orders)

    if not a.cancel:
        print("취소하려면 --cancel 옵션을 붙여 다시 실행하세요.")
        return

    # ---- 선택 ---------------------------------------------------------------
    try:
        raw = input("취소할 번호를 입력하세요 (예: 1,3 / 2-4 / all, 엔터=종료): ")
        if not raw.strip():
            print("종료합니다.")
            return
        targets = [orders[i] for i in parse_selection(raw, len(orders))]
    except ValueError as e:
        raise SystemExit(f"입력 오류: {e}") from None

    # ---- 최종 확인 ------------------------------------------------------------
    print(f"\n다음 {len(targets)}건을 전량 취소합니다 (거래소 {a.exch}):")
    for r in targets:
        side_txt = "매수" if str(r["deal_cls_code"]) == "2" else "매도"
        print(f"  - 주문 {int(r['oder_no'])} · {r['kor_issu_abwr_name']} {side_txt} "
              f"{int(r['oder_unpr']):,}원 × 미체결 {int(r['ncnt_qty']):,}주")
    if input("진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
        print("취소하지 않고 종료합니다.")
        return

    results = cancel_selected(targets, a.exch)

    print("\n결과")
    for r, msg in results:
        print(f"  주문 {int(r['oder_no'])} ({r['kor_issu_abwr_name']}): {msg}")

    # ---- 사후 확인: 접수 성공 ≠ 취소 확정 -----------------------------------------
    try:
        time.sleep(1)
        still = {int(r["oder_no"]) for r in find_open_orders(fetch_orders(date, side, a.iscd), a.api_only)}
        left = [int(r["oder_no"]) for r, _ in results if int(r["oder_no"]) in still]
        if left:
            print(f"\n[확인] 아직 미체결로 남아 있는 주문: {left}  (반영 지연이거나 취소 실패일 수 있습니다)")
        else:
            print("\n[확인] 선택한 주문이 미체결 목록에서 모두 사라졌습니다.")
    except MeritzError as e:
        print(f"\n[확인 생략] 재조회 실패: {e}")


if __name__ == "__main__":
    main()
