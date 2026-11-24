"""메리츠증권 Open API — 국내주식 주문 취소

  POST /trading/v1/orders/cancel   (TR: obtrs_oder_101a, TPS 10)

!! 실제 주문이 취소됩니다 (운영 서버로 전송).
   기본 동작은 '미리보기'이며, --yes 를 붙여야 실제로 전송됩니다.

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿

사용 예
  # 1) 미리보기 (전송 안 함)
  python meritz_cancel_order.py 005930 135

  # 2) 실제 전량 취소
  python meritz_cancel_order.py 005930 135 --yes

  # 3) 일부 수량만 취소 (5주)
  python meritz_cancel_order.py 005930 135 --qty 5 --yes

  # 4) NXT 거래소 주문 취소
  python meritz_cancel_order.py 005930 135 --exch 02 --yes

원주문번호(orgl_oder_no)는 주문 접수 응답 또는 주문내역 조회의 oder_no 입니다.
이미 정정한 주문이라면 '가장 최근 정정 건'의 번호를 넣어야 합니다.
"""
from __future__ import annotations

import argparse
import os
import time
import uuid
from typing import Any

import requests

BASE_URL = os.getenv("MERITZ_BASE_URL", "https://openapi.imeritz.com:9443").rstrip("/")
APP_KEY = os.getenv("MERITZ_APP_KEY")
APP_SECRET = os.getenv("MERITZ_APP_SECRET")
TIMEOUT = 15

# 이 값이면 사용자가 내용을 확인한 뒤 warn_cnfr_yn="Y" 로 재전송해 접수할 수 있다.
RESEND_OK = frozenset("1 3 4 6 7 9 a c d f g".split())

_token: dict[str, Any] = {"value": None, "expires_at": 0.0}


class MeritzError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class MeritzWarning(MeritzError):
    """경고가 붙어 '접수되지 않은' 상태. rsp_cd 가 0001 이어도 접수된 게 아니다."""

    def __init__(self, message: str, *, code: str, resendable: bool, body: Any = None):
        super().__init__(message, body=body)
        self.warn_cls_code = code
        self.resendable = resendable


def _mac_address() -> str:
    """게이트웨이 필수 헤더. 없으면 IGW50024 로 거절된다."""
    n = uuid.getnode()
    return "".join(f"{(n >> s) & 0xFF:02X}" for s in range(40, -1, -8))


def get_token() -> str:
    """접근토큰 발급. 만료 전까지 재사용 (form-urlencoded 필수)."""
    if _token["value"] and time.time() < _token["expires_at"]:
        return _token["value"]
    if not APP_KEY or not APP_SECRET:
        raise MeritzError("MERITZ_APP_KEY / MERITZ_APP_SECRET 환경변수가 필요합니다.")

    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": APP_KEY,
            "client_secret": APP_SECRET,
            "scope": "login",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise MeritzError(f"토큰 발급 실패 (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + int(data.get("expires_in", 43200)) - 60
    return _token["value"]


def _check(status: int, body: Any) -> dict:
    """응답 판정. HTTP 200 / rsp_cd 만으로 성공을 단정하지 않는다."""
    # 오류는 종류와 무관하게 HTTP 500 으로 내려오고 사유는 rsp_msg 에 담긴다.
    if status != 200:
        e = body if isinstance(body, dict) else {}
        detail = " ".join(str(e.get(k)) for k in ("rsp_cd", "rsp_msg", "rsp_sub_msg") if e.get(k))
        raise MeritzError(f"HTTP {status}" + (f": {detail}" if detail else ""),
                          status=status, body=body)
    if not isinstance(body, dict):
        raise MeritzError(f"예상치 못한 응답: {body!r}", status=status, body=body)

    # 경고 확인이 먼저: rsp_cd 가 "0001" 이어도 warn_cls_code 가 "0" 이 아니면 미접수.
    data = body.get("data") or {}
    warn = str(data.get("warn_cls_code", "0")).strip()
    if warn and warn != "0":
        ok = warn in RESEND_OK
        msg = (str(data.get("warn_msg") or "").strip() or "확인이 필요한 주문입니다")
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


def cancel_order(
    iscd: str,
    orgl_oder_no: int,
    *,
    whole: bool = True,
    qty: int | None = None,
    exch_kind_code: str = "01",
    warn_confirmed: bool = False,
    confirm: bool = False,
) -> dict:
    """국내주식 주문 취소.

    iscd           : 종목코드. 6자리/A+6자리 모두 받아 A+6자리로 보낸다.
    orgl_oder_no   : 취소할 주문의 번호 (정정했다면 직전 정정 건 번호)
    whole          : True=전량 취소, False=일부 취소 (qty 필수)
    qty            : 일부 취소 시 취소할 수량
    exch_kind_code : "01" KRX / "02" NXT / "03" SOR  (원주문과 같은 거래소)
    warn_confirmed : 경고 문구를 확인했으면 True (warn_cnfr_yn="Y" 로 전송)
    confirm        : True 여야 실제로 전송. False 면 보낼 내용만 반환(미리보기).
    """
    code = iscd.strip().upper()
    if len(code) == 6:
        code = "A" + code
    if not (len(code) == 7 and code[0] == "A" and code[1:].isdigit()):
        raise ValueError("종목코드는 6자리 숫자 또는 A+6자리 숫자여야 합니다.")
    if exch_kind_code not in ("01", "02", "03"):
        raise ValueError('exch_kind_code 는 "01"(KRX) / "02"(NXT) / "03"(SOR) 중 하나여야 합니다.')
    if not whole and not qty:
        raise ValueError("일부 취소는 qty(취소 수량)가 필요합니다.")

    payload: dict[str, Any] = {
        "iscd": code,
        "orgl_oder_no": orgl_oder_no,
        "whol_rctf_cncl_yn": "Y" if whole else "N",
        "exch_kind_code": exch_kind_code,
        "warn_cnfr_yn": "Y" if warn_confirmed else "N",
    }
    if not whole:
        payload["odqt"] = qty

    if not confirm:
        return {"needs_confirmation": True, "endpoint": "POST /trading/v1/orders/cancel",
                "will_send": payload}

    resp = requests.post(
        f"{BASE_URL}/trading/v1/orders/cancel",
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


def main() -> None:
    p = argparse.ArgumentParser(description="메리츠증권 국내주식 주문 취소")
    p.add_argument("iscd", help="종목코드 (예: 005930 또는 A005930)")
    p.add_argument("order_no", type=int, help="취소할 원주문번호")
    p.add_argument("--qty", type=int, help="일부 취소 수량 (생략하면 전량 취소)")
    p.add_argument("--exch", default="01", choices=["01", "02", "03"],
                   help="01=KRX, 02=NXT, 03=SOR (기본 01)")
    p.add_argument("--yes", action="store_true", help="실제로 전송 (없으면 미리보기만)")
    a = p.parse_args()

    kwargs = dict(whole=a.qty is None, qty=a.qty, exch_kind_code=a.exch)

    try:
        preview = cancel_order(a.iscd, a.order_no, confirm=False, **kwargs)
        print("보낼 내용:", preview["will_send"])
        if not a.yes:
            print("\n[미리보기] 전송하지 않았습니다. 실제로 취소하려면 --yes 를 붙이세요.")
            return

        try:
            res = cancel_order(a.iscd, a.order_no, confirm=True, **kwargs)
        except MeritzWarning as w:
            print(f"\n[경고] {w}")
            if not w.resendable:
                raise SystemExit("보류 상태라 접수되지 않았습니다.") from None
            if input("내용을 확인했고 계속 진행하시겠습니까? (y/N) ").strip().lower() != "y":
                raise SystemExit("취소 요청을 중단했습니다.") from None
            res = cancel_order(a.iscd, a.order_no, confirm=True, warn_confirmed=True, **kwargs)
    except (MeritzError, ValueError) as e:
        raise SystemExit(f"실패: {e}") from None

    print("\n취소 접수 완료")
    print("취소 주문번호:", (res.get("data") or {}).get("oder_no"))
    print("메시지:", res.get("rsp_msg"))
    print("※ 접수 성공이 곧 취소 확정은 아닙니다. 주문내역에서 최종 상태를 확인하세요.")


if __name__ == "__main__":
    main()
