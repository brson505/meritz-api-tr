"""메리츠증권 Open API — 국내주식 현재가 조회

  GET /market/v1/prices   (TR: ohhts_1001_001r, TPS 10)

준비
  pip install requests
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿

사용
  python meritz_current_price.py 005930
  python meritz_current_price.py 005930 --market TJ
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

_token: dict[str, Any] = {"value": None, "expires_at": 0.0}


class MeritzError(RuntimeError):
    pass


def _mac_address() -> str:
    """게이트웨이 필수 헤더. 없으면 IGW50024 로 거절된다."""
    n = uuid.getnode()
    return "".join(f"{(n >> s) & 0xFF:02X}" for s in range(40, -1, -8))


def get_token() -> str:
    """접근토큰 발급. 만료 전까지 재사용한다. (form-urlencoded 필수, JSON 은 EGW00116 거절)"""
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


def get_current_price(iscd: str, market: str = "J") -> dict:
    """현재가 조회.

    market: "J"=KRX, "NJ"=NXT, "TJ"=KRX+NXT 통합 (이 세 값만 사용)
    iscd  : 종목코드 6자리(005930) 또는 A+6자리(A005930)
    """
    if market not in ("J", "NJ", "TJ"):
        raise ValueError("market 은 J / NJ / TJ 중 하나여야 합니다.")

    resp = requests.get(
        f"{BASE_URL}/market/v1/prices",
        params={"mrkt_div_code": market, "iscd": iscd},
        headers={
            "authorization": f"Bearer {get_token()}",
            "mac_address": _mac_address(),
        },
        timeout=TIMEOUT,
    )

    # 오류는 종류와 무관하게 HTTP 500 으로 내려온다.
    if resp.status_code != 200:
        raise MeritzError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    data = body.get("data") or {}

    # 주의: 국내 시세 API 는 rsp_cd 에 널바이트가 섞여 올 수 있어
    # rsp_cd == "0000" 으로 성공을 판정하면 안 된다. data 로 판정한다.
    # 또한 없는 종목·잘못된 코드는 오류 없이 값이 0/빈 값으로 내려오므로
    # 단축종목코드(shrn_iscd)와 현재가가 실제로 채워졌는지 확인한다.
    if not data.get("shrn_iscd") or not data.get("stck_prpr"):
        raise MeritzError("조회 결과가 비어 있습니다. 종목코드를 확인하세요.")

    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="메리츠증권 현재가 조회")
    parser.add_argument("iscd", nargs="?", default="005930", help="종목코드 (기본: 005930)")
    parser.add_argument("--market", default="J", choices=["J", "NJ", "TJ"],
                        help="J=KRX, NJ=NXT, TJ=통합 (기본: J)")
    args = parser.parse_args()

    try:
        d = get_current_price(args.iscd, args.market)
    except MeritzError as e:
        raise SystemExit(str(e)) from None

    sign = {"1": "상한", "2": "▲", "3": "-", "4": "하한", "5": "▼"}.get(d["prdy_vrss_sign"], "")
    print(f"{d['kor_isnm']} ({d['shrn_iscd']})")
    print(f"현재가  : {d['stck_prpr']:,}원  {sign} {d['prdy_vrss']:,} ({d['prdy_ctrt']}%)")
    print(f"시/고/저: {d['oprc']:,} / {d['hprc']:,} / {d['lprc']:,}")
    print(f"거래량  : {d['acml_vol']:,}주")
    print(f"PER/PBR : {d['per']} / {d['pbr']}")
