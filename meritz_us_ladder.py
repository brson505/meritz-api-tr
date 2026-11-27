"""메리츠증권 Open API — 미국 주식 '기준가 -x% 매수 / +y% 매도' 반복 매매 엔진 (실시간 웹소켓)

이 파일은 (1) 매매 엔진(Ladder, Feed)과 (2) 한 종목을 명령행으로 돌리는 프로그램입니다.
여러 종목을 CSV 파일로 돌리려면 meritz_us_ladder_file.py 를 쓰세요 (이 엔진을 가져다 씁니다).

  python meritz_us_ladder.py AAPL 1 -1.0% +3.0% --exch NASDAQ                      # 계획만 출력
  python meritz_us_ladder.py AAPL 1 -1.0% +3.0% --exch NASDAQ --yes                # 실행 (고정 지정가)
  python meritz_us_ladder.py AAPL 1 -1.0% +3.0% --exch NASDAQ --price 100.00 --yes # 기준가를 직접 지정
  python meritz_us_ladder.py AAPL 1 -1.0% +3.0% --exch NASDAQ --trailing-stop 0.1% --yes   # 트레일링

기준가
  기본은 시작할 때의 현재가입니다. --price 100.00 을 주면 현재가 대신 그 값을 기준가로 씁니다.
  (이후에는 체결될 때마다 아래 규칙대로 기준가가 바뀝니다.)

모드 1 — 고정 지정가 (기본)
  기준가 −1.0% 에 지정가 매수, +3.0% 에 지정가 매도 주문을 걸어 둔다.
  한쪽이 체결되면 반대쪽을 취소하고 새 기준가로 다시 건다.
  새 기준가: 주문 가격과 무관하게 기존 기준가에서 계산 — 매수 체결이면 '기존 기준가 × (1 + 매수율)',
  매도 체결이면 '기존 기준가 / (1 + 매수율)'
  (예: 매수율 -1% 에서 기준가 100 → 매수 후 99, 기준가 99 → 매도 후 100).

모드 2 — 트레일링 (--trailing-stop 0.1%)
  주문을 미리 걸어 두지 않고 실시간 체결 시세를 계속 보면서 추적한다.
    매수: 가격이 매수 목표(기준가 −1.0%) 이하로 내려가면 최저점을 추적하다가,
          최저점 대비 0.1% 반등하면 매수 주문을 낸다.
    매도: 가격이 매도 목표(기준가 +3.0%) 이상으로 오르면 최고점을 추적하다가,
          최고점 대비 0.1% 하락하면 매도 주문을 낸다.
  주문 가격은 현재가가 아니라 목표가입니다 — 매수는 기준가×(1+매수율), 매도는 기준가×(1+매도율) 지정가.
  (그래서 반등·하락한 현재가가 목표가보다 불리하면 체결되지 않을 수 있고, --fill-timeout 뒤 취소하고 다시 추적합니다.)
  --fill-timeout(기본 30초) 안에 체결되지 않으면 취소하고 다시 추적합니다.
  체결되면 모드 1과 같은 규칙으로 새 기준가를 잡는다 — 매수는 '기존 기준가 × (1 + 매수율)', 매도는 '기존 기준가 / (1 + 매수율)'.
  이후 같은 방식으로 반복.

실시간 웹소켓 (기본)
  시세   : 해외주식 현재가(정규장) 'ohhts_exst_cntg' 를 구독해 체결이 올 때마다 판단한다 (트레일링).
  체결통보: 'ohhts_cntg_rslt' 를 구독해 알림이 오면 곧바로 주문내역을 확인한다.
           알림은 '신호'로만 씁니다 — 체결 여부의 근거는 항상 주문내역 조회입니다.
           알림이 오지 않아도 --poll 초마다 주문내역을 확인하므로 동작에는 지장이 없습니다.
  연결이 끊기면 자동으로 다시 연결해 구독합니다. 정규장 시세만 받습니다
  (주간거래·애프터마켓 시세는 다른 구독 코드라 이 프로그램은 구독하지 않습니다).
  --no-ws 를 주면 웹소켓 없이 예전처럼 REST 로 주기적으로 확인합니다.

트레일링 상태 출력 (--show-trail)
  추적을 시작하면 시세가 올 때마다 '현재가 · 최저/최고 · 주문이 나가는 트리거 가격'을 출력합니다.
  추적을 시작하기 전(목표에 닿기 전)에는 --status-every 초(기본 10)마다 현재가와 목표까지의 거리를 출력합니다.
  매수 불가(잔고현금 부족·보유 한도)나 매도 불가(잔고수량 부족)인 쪽은 그 사유를 함께 보여 줍니다.

매도 규칙 (중요)
  이 프로그램은 '자기가 산 수량'(+ --position 으로 알려 준 수량)만 매도합니다.
  처음에 보유 수량이 0 이면 매도는 첫 매수가 체결된 뒤부터 시작됩니다.
  이미 가진 주식을 매도 대상으로 삼으려면 --position N 으로 수량을 알려 주세요
  (실제 매도가능수량을 넘으면 시작하지 않습니다).

안전장치 (모두 조정 가능)
  --max-position  보유 한도. 넘으면 더 사지 않는다.        (기본: 주문수량 × 5)
  --max-fills     체결이 이만큼 나면 멈춘다.                (기본: 20)
  Ctrl+C / 오류 / 이상 상황 → 걸어 둔 주문을 취소하고 종료 (--keep-orders 면 그대로 둠)
  애매한 상황(양쪽 동시 체결, 일부 체결 후 취소 등)에서는 자동으로 계속하지 않고 멈춥니다.
  지연 시세(dely_yn=1)가 오면 트레일링은 멈춥니다 (지연된 가격으로 주문하지 않기 위해).

!! 운영 서버로 실제 주문이 나갑니다. 하락장에서는 -x% 마다 계속 사게 되어
   --max-position 까지 손실이 커질 수 있습니다. 주문은 당일 유효 주문일 수 있어
   장이 끝나면 사라질 수 있습니다 (사라지면 감지하고 멈춥니다).
   트레일링 모드는 프로그램이 켜져 있고 시세를 받는 동안에만 동작합니다 (꺼지면 보호 주문도 없음).

준비
  pip install requests websockets
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿
  (meritz_cancel_order.py, meritz_us_orders.py, meritz_us_cancel.py 와 같은 폴더에 두세요)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
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
from meritz_us_cancel import _symbol, cancel_us_order, fetch_history
from meritz_us_orders import EXCH_NAME, EXCHANGES, Order, place_order

PCT_RE = re.compile(r"^[+-]\d+(\.\d+)?%$")
VALUE_OPTS = {"--trailing-stop", "--slippage"}             # 값으로 % 를 받는 옵션
QUOTE_EXCH = {"0537": "OQ", "0321": "NY", "0066": "AX"}   # 주문용 거래소 코드 → 시세용 거래소 코드
CENT = Decimal("0.01")
KST = ZoneInfo("Asia/Seoul")
MAX_UNFILLED = 5   # 트레일링 주문이 연속으로 체결되지 않으면 멈춘다

WS_URL = os.getenv("MERITZ_WS_URL", "wss://openapi.imeritz.com:29443/websocket")
TICK_TR = "ohhts_exst_cntg"    # (실시간) 해외주식 현재가 (정규장)
FILL_TR = "ohhts_cntg_rslt"    # (실시간) 주문 체결 통보
# 다시 연결해도 소용없는 오류 — 토큰/구독 코드/세션 한도 문제
FATAL_WS_CODES = {"EGW00103", "EGW00121", "EGW00202", "WSC00106", "WSC00108", "WSC00110"}

_IO = threading.Lock()         # 여러 종목 스레드가 화면·로그 파일을 함께 쓰므로


class StopBot(Exception):
    """이상 상황. 걸어 둔 주문을 정리하고 멈춘다."""


class FeedError(Exception):
    """웹소켓에서 다시 연결해도 해결되지 않는 오류."""


# --------------------------------------------------------------------------- #
# 시세 / 잔고 (REST, 조회 전용)
# --------------------------------------------------------------------------- #
def _get(path: str, params: dict[str, str]) -> dict:
    resp = requests.get(
        f"{BASE_URL}{path}", params=params,
        headers={"authorization": f"Bearer {get_token()}", "mac_address": _mac_address()},
        timeout=TIMEOUT,
    )
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    # 오류는 HTTP 500 으로 오고 본문에는 data 가 없다 — 먼저 상태부터 본다.
    if resp.status_code != 200 or not isinstance(body, dict):
        detail = ""
        if isinstance(body, dict):
            detail = " ".join(str(body.get(k)) for k in ("rsp_cd", "rsp_msg", "rsp_sub_msg") if body.get(k))
        raise MeritzError(f"조회 실패 {path} (HTTP {resp.status_code}) {detail}".strip())
    return body


def get_price(symbol: str, exch: str, delayed: bool = False) -> Decimal:
    """현재가. 시세용 거래소 코드(OQ/NY/AX)를 쓴다 — 주문용 벤더 코드를 넣으면 빈 값이 온다."""
    body = _get("/market/v1/overseas/prices", {
        "mrkt_div_code": "OV", "mrkt_cls_code": QUOTE_EXCH[exch],
        "iscd": symbol, "dely_rltm_cls_code": "1" if delayed else "0",
    })
    if body.get("rsp_cd") != "0000":
        raise MeritzError(f"시세 오류 {body.get('rsp_cd')} — {body.get('rsp_msg')}")
    data = body.get("data") or {}
    # 잘못된 심볼·거래소는 오류 없이 종목코드가 비고 가격이 0 으로 온다.
    price = Decimal(str(data.get("prpr") or "0"))
    if not data.get("iscd") or price <= 0:
        raise MeritzError(f"{symbol} 시세를 가져오지 못했습니다. 심볼과 거래소를 확인하세요.")
    return price


def get_all_holdings() -> dict[str, tuple[Decimal, Decimal]]:
    """계좌의 해외 보유 종목 전체 → {심볼: (보유수량, 매도가능수량)}. 대출 잔고는 제외."""
    out: dict[str, tuple[Decimal, Decimal]] = {}
    params: dict[str, str] = {}
    seen: set[str] = set()
    for _ in range(20):
        body = _get("/accounts/v1/me/overseas/holdings", params)
        if body.get("rsp_cd") not in ("5766", "5820", "5762"):
            raise MeritzError(f"잔고 오류 {body.get('rsp_cd')} — {body.get('rsp_msg')}")
        for r in body.get("data") or []:
            if str(r.get("rmnd_clcd")) == "3":
                continue
            sym = str(r.get("ovrs_stck_symb_iscd")).upper()
            held, sell = out.get(sym, (Decimal(0), Decimal(0)))
            out[sym] = (held + Decimal(str(r.get("rmnd_dqty") or "0")),
                        sell + Decimal(str(r.get("seln_able_dqty") or "0")))
        key = body.get("tr_cont_key")
        if body.get("tr_cont") != "1" or not key or key in seen:
            break
        seen.add(key)
        params = {"tr_cont": "1", "tr_cont_key": key}   # 키는 한 글자도 고치지 않는다
    return out


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else "0").strip())
    except InvalidOperation:
        return Decimal(0)


def print_account() -> None:
    """시작할 때 계좌 현황을 보여 준다: 해외 보유 종목(수량), 외화 예수금, 원화 주문가능금액. 조회만 하며,
    어느 하나가 실패해도 나머지는 보여 주고 프로그램은 계속 진행한다."""
    print("=" * 70)
    print("계좌 현황")
    # 1) 보유 종목
    try:
        rows: list[dict] = []
        params: dict[str, str] = {}
        seen: set[str] = set()
        for _ in range(20):
            body = _get("/accounts/v1/me/overseas/holdings", params)
            if body.get("rsp_cd") not in ("5766", "5820", "5762"):
                raise MeritzError(f"{body.get('rsp_cd')} — {body.get('rsp_msg')}")
            rows += [x for x in body.get("data") or [] if str(x.get("rmnd_clcd")) != "3"]
            key = body.get("tr_cont_key")
            if body.get("tr_cont") != "1" or not key or key in seen:
                break
            seen.add(key)
            params = {"tr_cont": "1", "tr_cont_key": key}
        rows = [x for x in rows if _dec(x.get("rmnd_dqty")) > 0]
        if not rows:
            print("  보유 종목: 없음")
        else:
            print(f"  보유 종목 {len(rows)}개")
            for x in rows:
                ex = {"0537": "NASDAQ", "0321": "NYSE", "0066": "AMEX"}.get(str(x.get("ovrs_exch_vndr_idno")), "")
                qty, sell = _dec(x.get("rmnd_dqty")).normalize(), _dec(x.get("seln_able_dqty")).normalize()
                pl, rate = _dec(x.get("vltn_prls_camt")), _dec(x.get("vltn_ert"))
                print(f"    {str(x.get('ovrs_stck_symb_iscd')):<8}{ex:<7} 보유 {qty:f}주 (매도가능 {sell:f}주) · "
                      f"평단 ${_dec(x.get('avrg_unpr')):,.4f} · 현재가 ${_dec(x.get('psnt_prc')):,.4f} · "
                      f"평가손익 {rate:+.2f}% ({x.get('crcd') or 'USD'} {pl:+,.2f}) · {str(x.get('isnm') or '').strip()}")
    except (MeritzError, requests.RequestException) as e:
        print(f"  보유 종목: 조회하지 못했습니다 ({e})")
    # 2) 외화 예수금 (이 값이 해외주식 매수에 쓰는 달러)
    try:
        body = _get("/accounts/v1/me/overseas/currencies", {})
        if body.get("rsp_cd") not in ("5766", "5820", "5762"):
            raise MeritzError(f"{body.get('rsp_cd')} — {body.get('rsp_msg')}")
        cur = body.get("data") or []
        if not cur:
            print("  외화 예수금: 없음")
        for c in cur:
            print(f"  외화 예수금 {c.get('crcd')}: {_dec(c.get('frcr_amt')):,.2f} "
                  f"(추정 {_dec(c.get('estm_frcr_amt')):,.2f}, 평가환율 {_dec(c.get('vltn_exrt')):,.2f}원)")
    except (MeritzError, requests.RequestException) as e:
        print(f"  외화 예수금: 조회하지 못했습니다 ({e})")
    # 3) 원화 예수금 / 주문가능금액
    try:
        body = _get("/accounts/v1/me/deposit", {})
        if body.get("rsp_cd") != "5766":
            raise MeritzError(f"{body.get('rsp_cd')} — {body.get('rsp_msg')}")
        d = body.get("data") or {}
        print(f"  원화 예수금 ₩{_dec(d.get('tfam')):,.0f} · 주문가능 현금 ₩{_dec(d.get('oder_able_cash_amt')):,.0f} "
              f"· 출금가능 ₩{_dec(d.get('drng_able_amt')):,.0f}")
    except (MeritzError, requests.RequestException) as e:
        print(f"  원화 예수금: 조회하지 못했습니다 ({e})")
    print("=" * 70)


def get_holdings(symbol: str) -> tuple[Decimal, Decimal]:
    """(보유수량, 매도가능수량) — 한 종목."""
    return get_all_holdings().get(symbol.upper(), (Decimal(0), Decimal(0)))


# --------------------------------------------------------------------------- #
# 실시간 웹소켓
# --------------------------------------------------------------------------- #
class Feed(threading.Thread):
    """웹소켓 수신 스레드. 연결이 끊기면 스스로 다시 연결하고 구독한다. 여러 종목을 한 연결로 받는다.

    - 체결 시세(TICK_TR)는 종목별 큐(queue_for)에 가격으로 쌓는다. (tick_keys 에 있는 종목만 구독)
    - 주문 체결 통보(FILL_TR)는 내용을 읽지 않고 등록된 이벤트(add_listener)만 켠다 —
      주문내역을 곧바로 확인하라는 신호.
    """

    def __init__(self, tick_keys: list[str], log: Callable[..., None]):
        super().__init__(daemon=True, name="ws-feed")
        self.tick_keys = list(dict.fromkeys(tick_keys))
        self.log = log
        self.queues: dict[str, queue.Queue[Decimal]] = {k: queue.Queue() for k in self.tick_keys}
        self.listeners: list[threading.Event] = []
        self.fills_seen = 0
        self.last_tick: dict[str, float] = {}      # 종목별 마지막 시세 수신 시각 (monotonic)
        self.delayed_keys: set[str] = set()        # 지연 시세(dely_yn=1)가 온 종목
        self.key_errors: dict[str, str] = {}       # 종목별 구독 실패 사유
        self.connected = False
        self.fatal: str | None = None
        self._halt = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._ws: Any = None

    def queue_for(self, key: str) -> "queue.Queue[Decimal]":
        return self.queues[key]

    def add_listener(self, ev: threading.Event) -> None:
        self.listeners.append(ev)

    # ---- 메시지 ------------------------------------------------------------
    @staticmethod
    def _sub(token: str, tr_cd: str, tr_key: str | None, tr_type: str = "1") -> str:
        body: dict[str, str] = {"tr_cd": tr_cd}
        if tr_key:
            body["tr_key"] = tr_key
        return json.dumps({"header": {"token": f"Bearer {token}", "tr_type": tr_type}, "body": body})

    def _route(self, header: dict, body: dict) -> str | None:
        """시세 메시지가 어느 종목의 것인지 가린다 (body.iscd 또는 header.tr_key)."""
        cands = [str(body.get("iscd") or ""), str(header.get("tr_key") or "")]
        for c in cands:
            if c in self.queues:
                return c
        sym = next((c.split("@")[-1].upper() for c in cands if c), "")
        matches = [k for k in self.queues if k.split("@")[-1].upper() == sym]
        if len(matches) == 1:
            return matches[0]
        if len(self.queues) == 1:
            return next(iter(self.queues))
        return None

    def _handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        header = msg.get("header") or {}

        # 오류는 봉투 없이 최상위로 온다.
        if "rsp_cd" in msg and not header:
            text = f"{msg['rsp_cd']} — {msg.get('rsp_msg')}"
            if str(msg["rsp_cd"]) in FATAL_WS_CODES:
                raise FeedError(text)
            raise RuntimeError(text)          # 일시적일 수 있으니 다시 연결해 본다

        # 구독 응답에는 header.rsp_cd 가 있고, 데이터에는 없다.
        if "rsp_cd" in header:
            code, tr, key = str(header["rsp_cd"]), header.get("tr_cd"), header.get("tr_key")
            if code == "00000":
                self.log("WS", f"구독 완료 — {tr or ''} {key if key in self.queues else ''}".rstrip())
                return
            text = f"구독 실패 {code} — {header.get('rsp_msg')} ({tr or '?'} {key or ''})".rstrip()
            if tr == FILL_TR or (tr is None and not self.tick_keys):
                self.log("WS", text + " — 체결통보 없이 주문내역 조회만으로 계속합니다.")
                return
            if key in self.queues:                 # 그 종목만 시세를 못 받는다 — 그 종목 스레드가 멈춘다
                self.key_errors[key] = text
                self.log("WS", text)
                return
            raise FeedError(text)

        body = msg.get("body")
        if not isinstance(body, dict):
            return                              # keepalive 등 빈 프레임
        tr = header.get("tr_cd") or (TICK_TR if "prpr" in body else None)
        if tr == TICK_TR:
            key = self._route(header, body)
            if key is None:
                return
            try:
                price = Decimal(str(body.get("prpr") or "0"))
            except InvalidOperation:
                return
            if price <= 0:
                return
            if str(body.get("dely_yn")) == "1":
                self.delayed_keys.add(key)
            self.last_tick[key] = time.monotonic()
            self.queues[key].put(price)
        elif tr == FILL_TR:
            self.fills_seen += 1
            for ev in list(self.listeners):
                ev.set()

    # ---- 연결 루프 -----------------------------------------------------------
    async def _main(self) -> None:
        import websockets
        loop = asyncio.get_running_loop()
        backoff = 1.0
        while not self._halt.is_set():
            err = "연결이 종료되었습니다"
            try:
                token = await loop.run_in_executor(None, get_token)
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self.connected = True
                    self.log("WS", "연결됨")
                    for key in self.tick_keys:
                        await ws.send(self._sub(token, TICK_TR, key))
                        if len(self.tick_keys) > 1:
                            await asyncio.sleep(0.12)      # 구독 요청도 초당 10건 제한
                    await ws.send(self._sub(token, FILL_TR, None))
                    backoff = 1.0
                    async for raw in ws:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except FeedError as e:
                self.fatal = str(e)
                return
            except Exception as e:                     # 연결 실패·끊김·일시 오류 → 재연결
                err = f"{type(e).__name__}: {e}"
            self._ws = None
            self.connected = False
            if self._halt.is_set():
                return
            self.log("WS끊김", f"{err} — {backoff:g}초 뒤 다시 연결합니다.")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._main())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception as e:                         # 예상 밖 오류는 메인이 알 수 있게 남긴다
            self.fatal = f"{type(e).__name__}: {e}"
        finally:
            self.connected = False
            self._loop.close()

    # ---- 다른 스레드에서 쓰는 함수 ---------------------------------------------
    async def _close(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()          # 정상 종료(1000) — 수신 루프가 끝나고 _main 이 빠져나온다
            except Exception:
                pass
        elif self._task is not None:
            self._task.cancel()           # 연결 중이거나 재연결 대기 중

    def stop(self) -> None:
        self._halt.set()
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._close(), self._loop)
            except RuntimeError:
                pass

    def check(self, key: str | None = None) -> None:
        """치명적인 문제가 있으면 StopBot. key 를 주면 그 종목의 구독 실패·지연 시세도 본다."""
        if self.fatal:
            raise StopBot(f"웹소켓 오류: {self.fatal}")
        if key is not None:
            if key in self.key_errors:
                raise StopBot(f"시세 구독 실패: {self.key_errors[key]}")
            if key in self.delayed_keys:
                raise StopBot("지연 시세(dely_yn=1)가 수신되고 있습니다. 지연된 가격으로는 트레일링 주문을 내지 않습니다. "
                              "실시간 시세 권한을 확인하세요.")
        if not self.is_alive() and not self._halt.is_set():
            raise StopBot("웹소켓 수신 스레드가 종료되었습니다.")


# --------------------------------------------------------------------------- #
# 가격 계산
# --------------------------------------------------------------------------- #
def target_prices(base: Decimal, down: Decimal, up: Decimal) -> tuple[Decimal, Decimal]:
    """기준가에서 매수 목표(−)와 매도 목표(+). 센트 단위로 매수는 내림, 매도는 올림.

    1달러 이상 종목의 호가단위는 0.01달러로 가정한다.
    """
    buy = (base * (1 + down / 100)).quantize(CENT, rounding=ROUND_FLOOR)
    sell = (base * (1 + up / 100)).quantize(CENT, rounding=ROUND_CEILING)
    return buy, sell


# --------------------------------------------------------------------------- #
# 봇
# --------------------------------------------------------------------------- #
@dataclass
class Live:
    side: str          # "buy" | "sell"
    price: Decimal
    qty: int
    oder_no: int
    oder_date: str


class Ladder:
    """한 종목의 반복 매매. 여러 종목이면 종목마다 하나씩 만들어 각자 스레드에서 돌린다.

    a(Namespace) 에서 읽는 값
      필수: symbol, exch_code, qty, position, max_position(None=무제한), max_fills, poll,
            keep_orders, trailing(None=고정 지정가), slippage, fill_timeout
      선택: cash(Decimal|None — 잔고현금 장부, None 이면 현금 제한 없음),
            on_update(callable — 체결로 기준가·잔고가 바뀔 때마다 호출),
            ask(callable — 경고 확인 질문), stop_event(threading.Event — 종료 요청)
    """

    def __init__(self, a: argparse.Namespace, down: Decimal, up: Decimal):
        self.symbol, self.exch, self.qty = a.symbol, a.exch_code, a.qty
        self.down, self.up = down, up
        self.poll, self.max_pos, self.max_fills = a.poll, a.max_position, a.max_fills
        self.keep_orders = a.keep_orders
        self.trailing: Decimal | None = a.trailing        # 트레일링 % (None 이면 고정 지정가 모드)
        self.slip: Decimal = a.slippage
        self.fill_timeout: float = a.fill_timeout
        self.position = Decimal(a.position)               # 이 프로그램이 매도해도 되는 보유 수량(장부)
        self.cash: Decimal | None = getattr(a, "cash", None)   # 이 프로그램이 쓸 수 있는 현금(장부)
        self.on_update: Callable[["Ladder"], None] | None = getattr(a, "on_update", None)
        self.ask: Callable[[str], str] = getattr(a, "ask", None) or input
        self.stop_event: threading.Event = getattr(a, "stop_event", None) or threading.Event()
        self.show_trail: bool = bool(getattr(a, "show_trail", False))      # 추적 중일 때마다 상태를 출력
        self.status_every: float = float(getattr(a, "status_every", 0) or 0)   # 대기 중 상태를 출력하는 주기(초)
        self._last_status = 0.0
        self.base: Decimal = Decimal(0)
        self.orders: dict[str, Live] = {}
        self.fills = 0
        self.missing = {"buy": 0, "sell": 0}
        self.partial: dict[str, Decimal] = {}
        self.ext: dict[str, Decimal | None] = {"buy": None, "sell": None}   # 트레일링 극값(최저/최고)
        self.unfilled = 0
        self.buy_hold = False                             # 현금 부족으로 매수를 보류 중 (체결이 나면 해제)
        self.feed: Feed | None = None                     # 웹소켓 (--no-ws 면 None)
        self.tick_key: str | None = None
        self.tick_q: "queue.Queue[Decimal] | None" = None
        self.fill_event = threading.Event()               # 체결통보 신호 (웹소켓이 켠다)
        self.fired = False                                # 방금 트레일링 주문을 냈는가 (오래된 시세 정리용)
        self._last_say = 0.0
        self.log_path = Path(f"us_ladder_{self.symbol}_{datetime.now(KST):%Y%m%d_%H%M%S}.csv")

    def attach_feed(self, feed: Feed, tick_key: str | None) -> None:
        """웹소켓 연결(공유 가능)을 이 종목에 붙인다. tick_key 가 있으면 그 종목의 시세 큐를 받는다."""
        self.feed = feed
        feed.add_listener(self.fill_event)
        if tick_key:
            self.tick_key = tick_key
            self.tick_q = feed.queue_for(tick_key)

    # ---- 기록 --------------------------------------------------------------
    def say(self, msg: str) -> None:
        """화면에만 출력 (CSV 에는 남기지 않는다)."""
        with _IO:
            print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {self.symbol:<6} {msg}")

    def say_throttled(self, msg: str, every: float = 1.0) -> None:
        """시세가 초당 여러 번 와도 화면이 넘치지 않게 1초에 한 번만 출력."""
        now = time.monotonic()
        if now - self._last_say >= every:
            self._last_say = now
            self.say(msg)

    def log(self, event: str, msg: str = "", **kw: Any) -> None:
        ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        with _IO:
            print(f"[{ts}] {self.symbol:<6} {event:<8} {msg}")
            new = not self.log_path.exists()
            with self.log_path.open("a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "event", "message", "base", "position", "cash", "fills", "detail"])
                w.writerow([ts, event, msg, self.base, self.position,
                            "" if self.cash is None else self.cash, self.fills,
                            " ".join(f"{k}={v}" for k, v in kw.items())])

    def notify(self) -> None:
        """기준가·잔고가 바뀌었음을 알린다 (파일 갱신 등). 실패해도 매매는 계속한다."""
        if self.on_update is not None:
            try:
                self.on_update(self)
            except Exception as e:                 # noqa: BLE001 — 파일 쓰기 오류로 매매를 멈추지 않는다
                self.log("파일오류", f"{type(e).__name__}: {e}")

    def nap(self, sec: float) -> None:
        """잠깐 기다린다. 웹소켓이 있으면 체결 통보가 오는 즉시 깨어난다."""
        if self.feed is not None:
            if self.fill_event.wait(sec):
                self.fill_event.clear()
        else:
            self.stop_event.wait(sec)

    # ---- 주문내역 ----------------------------------------------------------
    def history(self) -> list[dict]:
        start = (datetime.now(KST) - timedelta(days=2)).strftime("%Y%m%d")
        return fetch_history(start, "20991231", "0", "0")   # 체결·미체결 모두

    def _rows_of(self, rows: list[dict], live: Live) -> list[dict]:
        return [r for r in rows
                if int(r["oder_no"]) == live.oder_no and r["oder_date"] == live.oder_date
                and _symbol(r["iscd"]).upper() == self.symbol
                and str(r.get("oder_rctf_cncl_cls_code")) != "2"]

    def inspect(self, rows: list[dict], live: Live) -> tuple[bool, Decimal, str | None]:
        """(내역에서 찾았나, 체결수량 합계, 거부 사유). 체결수량은 같은 주문의 모든 행을 합한다."""
        mine = self._rows_of(rows, live)
        rejected = None
        for r in mine:
            if str(r.get("ovrs_stck_oder_stat_cls_code")) in ("1", "2"):
                rejected = str(r.get("ovrs_stck_rfus_reas") or "거부")
        return bool(mine), sum((Decimal(str(r.get("cntg_dqty") or "0")) for r in mine), Decimal(0)), rejected

    def fill_amount(self, rows: list[dict], live: Live) -> Decimal:
        """같은 주문의 체결금액(cntg_camt, 달러) 합계. 못 구하면 0."""
        return sum((Decimal(str(r.get("cntg_camt") or "0")) for r in self._rows_of(rows, live)), Decimal(0))

    # ---- 장부 ----------------------------------------------------------------
    def afford(self, price: Decimal) -> bool:
        """이 가격으로 1회 수량을 살 현금이 장부에 있는가."""
        return self.cash is None or self.cash >= price * self.qty

    def apply_fill(self, side: str, live: Live, amount: Decimal) -> None:
        """체결을 장부에 반영한다. 체결금액을 모르면 주문 가격 × 수량으로 계산한다(수수료는 반영하지 않음)."""
        cost = amount if amount > 0 else live.price * live.qty
        if side == "buy":
            self.position += live.qty
            if self.cash is not None:
                self.cash -= cost
        else:
            self.position -= live.qty
            if self.cash is not None:
                self.cash += cost
        self.fills += 1
        self.unfilled = 0
        self.buy_hold = False

    def next_base(self, side: str, fill_price: Decimal) -> Decimal:
        """체결 뒤의 새 기준가.
        주문 가격은 쓰지 않고 항상 기존 기준가에서 계산한다(고정 지정가·트레일링 동일).
        매수: 기존 기준가 × (1 + 매수율)  (기준가 100, 매수율 -1% → 99)
        매도: 기존 기준가 / (1 + 매수율)  (기준가 99, 매수율 -1% → 100)
        """
        factor = 1 + self.down / 100
        new = self.base * factor if side == "buy" else self.base / factor
        return new.quantize(Decimal("0.0001"))

    def ledger_text(self) -> str:
        cash = "" if self.cash is None else f", 잔고현금 ${self.cash:,.2f}"
        return f"보유 {self.position}{cash}, 누적 체결 {self.fills}"

    # ---- 주문 ----------------------------------------------------------------
    def submit(self, side: str, price: Decimal) -> Live:
        o = Order(0, side, self.symbol, self.exch, "limit", price, self.qty)
        try:
            try:
                res = place_order(o)
            except MeritzWarning as w:
                if not w.resendable:
                    raise StopBot(f"{side} 주문이 보류되었습니다: {w}") from None
                print(f"\n[경고] {self.symbol} {side} ${price} × {self.qty}: {w}")
                if self.ask("  내용을 확인했고 이 주문을 전송하시겠습니까? (y/N) ").strip().lower() != "y":
                    raise StopBot("경고 확인을 거절해 중단합니다.") from None
                res = place_order(o, warn_confirmed=True)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise StopBot(f"{side} 주문 응답을 받지 못했습니다({type(e).__name__}). 접수 여부를 "
                          f"주문내역에서 확인하세요.") from None
        except MeritzError as e:
            raise StopBot(f"{side} 주문 실패: {e}") from None

        oder_no = int((res.get("data") or {}).get("oder_no") or 0)
        # 취소·조회에 주문일자가 필요한데 접수 응답에는 없다 — 주문내역에서 찾아 확정한다.
        for _ in range(8):
            time.sleep(1)
            try:
                rows = self.history()
            except (MeritzError, requests.RequestException):
                continue   # 일시적 조회 오류 — 주문번호를 잃지 않도록 다시 시도
            for r in rows:
                if int(r["oder_no"]) == oder_no and _symbol(r["iscd"]).upper() == self.symbol \
                        and str(r.get("oder_rctf_cncl_cls_code")) != "2":
                    live = Live(side, price, self.qty, oder_no, r["oder_date"])
                    self.log("주문", f"{side} ${price} × {self.qty} (주문번호 {oder_no})")
                    return live
        raise StopBot(f"{side} 주문(번호 {oder_no})은 접수됐지만 주문내역에서 찾지 못했습니다. "
                      f"수동 확인이 필요합니다.")

    def cancel(self, live: Live, filled: Decimal) -> None:
        row = {"oder_date": live.oder_date, "oder_no": live.oder_no, "aspr_type_code": "2",
               "oder_dqty": str(live.qty), "cntg_dqty": str(filled)}
        try:
            try:
                cancel_us_order(row, confirm=True)
            except MeritzWarning as w:
                if not w.resendable:
                    raise
                print(f"\n[경고] {self.symbol} 취소 {live.side} 주문 {live.oder_no}: {w}")
                if self.ask("  확인했고 취소를 계속하시겠습니까? (y/N) ").strip().lower() != "y":
                    raise StopBot("취소 경고 확인을 거절해 중단합니다.") from None
                cancel_us_order(row, warn_confirmed=True, confirm=True)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise StopBot(f"{live.side} 주문 취소 응답이 없습니다({type(e).__name__}). 주문내역을 확인하세요.") from None
        except (MeritzError, ValueError) as e:
            raise StopBot(f"{live.side} 주문 {live.oder_no} 취소 실패: {e}") from None
        self.log("취소", f"{live.side} ${live.price} (주문번호 {live.oder_no})")

    def trail_sides(self, buy_t: Decimal) -> tuple[bool, bool, str, str]:
        """트레일링에서 지금 추적할 수 있는 쪽. (매수 가능, 매도 가능, 매수 불가 사유, 매도 불가 사유)"""
        can_buy, can_sell = self.limits()
        buy_note = "" if can_buy else "보유 한도"
        if can_buy and (self.buy_hold or not self.afford(buy_t)):
            can_buy, buy_note = False, "잔고현금 부족"     # 매도 체결로 현금이 생기면 풀린다
        return can_buy, can_sell, buy_note, ("" if can_sell else "잔고수량 부족")

    def trail_line(self, price: Decimal, low: Decimal | None, high: Decimal | None, t: Decimal) -> None:
        """--show-trail: 추적 중인 쪽의 극값과 주문 트리거 가격을 시세가 올 때마다 출력한다."""
        parts = []
        if low is not None:
            trig = low * (1 + t)
            parts.append(f"매수 추적중 · 최저 ${low} → 반등 ${trig:.4f} 이상이면 매수 (현재가 대비 {(trig / price - 1) * 100:+.3f}%)")
        if high is not None:
            trig = high * (1 - t)
            parts.append(f"매도 추적중 · 최고 ${high} → 하락 ${trig:.4f} 이하이면 매도 (현재가 대비 {(trig / price - 1) * 100:+.3f}%)")
        self.say(f"트레일링 · 현재가 ${price} · " + " | ".join(parts))

    def wait_line(self, price: Decimal, buy_t: Decimal, sell_t: Decimal, buy_note: str, sell_note: str) -> None:
        """추적을 시작하기 전(목표에 닿기 전) 상태를 --status-every 초마다 출력한다."""
        now = time.monotonic()
        if self.status_every <= 0 or now - self._last_status < self.status_every:
            return
        self._last_status = now
        b = f"매수목표 ${buy_t} ({(buy_t / price - 1) * 100:+.2f}%)" + (f" [매수 불가: {buy_note}]" if buy_note else "")
        s = f"매도목표 ${sell_t} ({(sell_t / price - 1) * 100:+.2f}%)" + (f" [매도 불가: {sell_note}]" if sell_note else "")
        self.say(f"트레일링 대기 · 현재가 ${price} · {b} · {s} — 목표에 닿으면 추적을 시작합니다")

    def limits(self) -> tuple[bool, bool]:
        """(지금 매수 가능?, 지금 매도 가능?) — 보유 한도와 보유 수량 기준. 현금은 afford() 로 따로 본다."""
        can_buy = self.max_pos is None or self.position + self.qty <= self.max_pos
        return can_buy, self.position >= self.qty

    # ======================= 모드 1: 고정 지정가 =======================
    def place_pair(self) -> None:
        buy_price, sell_price = target_prices(self.base, self.down, self.up)
        if buy_price < 1:
            raise StopBot(f"매수가 ${buy_price} — 1달러 미만 종목은 호가단위가 달라 지원하지 않습니다.")
        can_buy, can_sell = self.limits()
        self.log("기준가", f"${self.base} → 매수 ${buy_price} / 매도 ${sell_price} ({self.ledger_text()})")
        if can_buy and not self.afford(buy_price):
            can_buy = False
            self.log("잔고부족", f"잔고현금 ${self.cash:,.2f} 로는 ${buy_price} × {self.qty} 매수가 안 됩니다 — 매수 주문은 걸지 않습니다.")
        elif not can_buy:
            self.log("한도", f"보유 한도({self.max_pos}) 도달 — 매수 주문은 걸지 않습니다.")
        if not can_sell:
            self.log("안내", "매도할 보유 수량이 없어 매도 주문은 걸지 않습니다.")
        if not can_buy and not can_sell:
            raise StopBot("매수할 현금(또는 한도)도 매도할 수량도 없어 더 진행할 수 없습니다.")
        if can_buy:
            self.orders["buy"] = self.submit("buy", buy_price)
        if can_sell:
            self.orders["sell"] = self.submit("sell", sell_price)
        self.missing = {"buy": 0, "sell": 0}
        self.partial = {}

    def on_fill(self, side: str, amount: Decimal) -> None:
        live = self.orders.pop(side)
        self.apply_fill(side, live, amount)
        old_base = self.base
        self.base = self.next_base(side, live.price)   # 매수=기준가×(1+매수율), 매도=기준가/(1+매수율)
        self.log("체결", f"{'매수' if side == 'buy' else '매도'} ${live.price} × {live.qty} "
                         f"(주문번호 {live.oder_no}, 체결금액 ${amount if amount > 0 else live.price * live.qty:,.2f}) "
                         f"→ {self.ledger_text()}, 기준가 ${old_base} → ${self.base}")
        self.notify()            # 장부·기준가를 먼저 저장한다 (이후 단계에서 멈춰도 기록은 남도록)

        other_side = "sell" if side == "buy" else "buy"
        other = self.orders.get(other_side)
        if other:
            # 취소하려는 사이 반대쪽이 체결됐는지 먼저 본다.
            _, other_filled, _ = self.inspect(self.history(), other)
            if other_filled >= other.qty:
                raise StopBot("반대쪽 주문도 동시에 체결되었습니다. 수동 확인이 필요합니다.")
            self.cancel(other, other_filled)
            self.orders.pop(other_side)
            time.sleep(1)
            _, after, _ = self.inspect(self.history(), other)
            if after > 0:
                raise StopBot(f"취소한 {other_side} 주문이 {after}주 체결되어 있습니다. "
                              f"보유 수량이 계산과 다를 수 있어 수동 확인이 필요합니다.")

        if self.fills >= self.max_fills:
            raise StopBot(f"--max-fills({self.max_fills}) 에 도달해 종료합니다.")
        self.place_pair()

    def step_resting(self) -> None:
        rows = self.history()
        done: list[str] = []
        for side, live in list(self.orders.items()):
            found, filled, rejected = self.inspect(rows, live)
            if rejected:
                raise StopBot(f"{side} 주문 {live.oder_no} 이 거부되었습니다: {rejected}")
            self.missing[side] = 0 if found else self.missing[side] + 1
            if self.missing[side] >= 3:
                raise StopBot(f"{side} 주문 {live.oder_no} 이 주문내역에서 사라졌습니다 "
                              f"(장 마감 등으로 소멸했을 수 있음). 확인이 필요합니다.")
            if filled >= live.qty:
                done.append(side)
            elif filled > 0 and self.partial.get(side) != filled:
                self.partial[side] = filled
                self.log("일부체결", f"{side} ${live.price}: {filled}/{live.qty} — 전량 체결을 기다립니다.")
        if len(done) == 2:
            raise StopBot("두 주문이 모두 체결되었습니다. 수동 확인이 필요합니다.")
        if done:
            try:
                self.on_fill(done[0], self.fill_amount(rows, self.orders[done[0]]))
            except (MeritzError, requests.RequestException) as e:
                # 체결 처리는 중간에 멈추면 보유 수량·주문 상태가 어긋난다 — 재시도하지 않고 멈춘다.
                raise StopBot(f"체결 처리 중 오류: {e} — 상태를 수동으로 확인하세요.") from None

    # ======================= 모드 2: 트레일링 =======================
    def step_trailing(self, price: Decimal | None = None) -> None:
        """시세 한 건을 처리한다. price 가 없으면(--no-ws) REST 로 현재가를 조회한다."""
        assert self.trailing is not None
        if price is None:
            price = get_price(self.symbol, self.exch)   # 실패하면 run() 이 재시도
        buy_t, sell_t = target_prices(self.base, self.down, self.up)
        if buy_t < 1:
            raise StopBot(f"매수 목표 ${buy_t} — 1달러 미만 종목은 호가단위가 달라 지원하지 않습니다.")
        can_buy, can_sell, buy_note, sell_note = self.trail_sides(buy_t)
        if not can_buy and not can_sell:
            raise StopBot("매수할 현금(또는 한도)도 매도할 수량도 없어 더 진행할 수 없습니다.")
        t = self.trailing / 100

        # 1) 목표에 닿으면 추적 시작, 이후 더 유리한 쪽으로 가면 극값 갱신
        if can_buy and price <= buy_t:
            prev = self.ext["buy"]
            if prev is None:
                self.log("추적시작", f"매수: 현재가 ${price} ≤ 매수목표 ${buy_t} — 최저점을 추적합니다.")
            if prev is None or price < prev:
                self.ext["buy"] = price
                if prev is not None and not self.show_trail:
                    self.say_throttled(f"매수 추적: 최저점 ${price} → 반등 ${price * (1 + t):.4f} 이상이면 매수")
        if can_sell and price >= sell_t:
            prev = self.ext["sell"]
            if prev is None:
                self.log("추적시작", f"매도: 현재가 ${price} ≥ 매도목표 ${sell_t} — 최고점을 추적합니다.")
            if prev is None or price > prev:
                self.ext["sell"] = price
                if prev is not None and not self.show_trail:
                    self.say_throttled(f"매도 추적: 최고점 ${price} → 하락 ${price * (1 - t):.4f} 이하이면 매도")

        # 2) 극값에서 trailing-stop 만큼 되돌렸으면 주문
        low, high = self.ext["buy"], self.ext["sell"]
        if t == 0:   # 0%: 극값을 지난 뒤 처음으로 반대로 움직이면(오르다 내리면 매도, 내리다 오르면 매수) 바로 주문
            buy_fire = low is not None and price > low
            sell_fire = high is not None and price < high
        else:
            buy_fire = low is not None and price >= low * (1 + t)
            sell_fire = high is not None and price <= high * (1 - t)
        if low is None and high is None:
            self.wait_line(price, buy_t, sell_t, buy_note, sell_note)     # 아직 목표에 닿지 않음
        elif self.show_trail:
            self.trail_line(price, low, high, t)                          # 추적 중
        if buy_fire and sell_fire:
            raise StopBot("매수·매도 트리거가 동시에 발생했습니다. 시세를 확인하세요.")
        if buy_fire or sell_fire:
            try:
                self.fire("buy" if buy_fire else "sell", price)
            except (MeritzError, requests.RequestException) as e:
                raise StopBot(f"트레일링 주문 처리 중 오류: {e} — 상태를 수동으로 확인하세요.") from None

    def fire(self, side: str, price: Decimal) -> None:
        """트레일링 조건 충족 → 체결되기 쉬운 지정가로 주문하고 체결을 기다린다."""
        assert self.trailing is not None
        buy_t, sell_t = target_prices(self.base, self.down, self.up)
        limit = buy_t if side == "buy" else sell_t     # 주문 가격 = 목표가 (현재가·슬리피지 아님)
        if side == "buy" and not self.afford(limit):
            # 현금이 모자라게 된 경우 — 주문하지 않고 매도 체결로 현금이 생길 때까지 보류
            self.ext["buy"] = None
            self.buy_hold = True
            self.log("잔고부족", f"잔고현금 ${self.cash:,.2f} 로는 ${limit} × {self.qty} 매수가 안 됩니다 — 매수를 보류합니다.")
            return
        self.fired = True
        self.log("트리거", f"{side}: 극값 ${self.ext[side]} → 현재 ${price} (트레일링 {self.trailing}%) "
                           f"— 지정가 ${limit} 주문")
        live = self.submit(side, limit)
        self.orders[side] = live    # 기다리는 동안 종료 요청이 와도 정리할 수 있게 등록
        deadline = time.time() + self.fill_timeout

        filled = Decimal(0)
        while True:
            self.nap(1.0)           # 웹소켓이 있으면 체결 통보가 오는 즉시 깨어난다
            if self.stop_event.is_set():
                raise StopBot("종료 요청 — 걸어 둔 주문을 정리합니다.")
            try:
                rows = self.history()
            except (MeritzError, requests.RequestException):
                if time.time() > deadline + 30:
                    raise StopBot("체결 확인을 계속 못 하고 있습니다. 주문내역을 확인하세요.") from None
                continue
            _, filled, rejected = self.inspect(rows, live)
            if rejected:
                raise StopBot(f"{side} 주문 {live.oder_no} 이 거부되었습니다: {rejected}")
            if filled >= live.qty:
                break
            if time.time() < deadline:
                continue

            # 시간 안에 체결되지 않았다 → 취소하고, 그 사이 체결됐는지 다시 확인
            try:
                self.cancel(live, filled)
            except StopBot:
                _, filled, _ = self.inspect(self.history(), live)
                if filled >= live.qty:
                    break          # 취소하려는 사이 체결됨
                raise
            time.sleep(1)
            _, filled, _ = self.inspect(self.history(), live)
            if filled >= live.qty:
                break
            if filled > 0:
                raise StopBot(f"{side} 주문이 {filled}/{live.qty}주만 체결된 채 취소되었습니다. "
                              f"보유 수량이 계산과 다를 수 있어 수동 확인이 필요합니다.")
            self.orders.pop(side, None)
            self.unfilled += 1
            self.ext[side] = None   # 다시 추적
            self.log("미체결", f"{side} ${limit} 주문이 {self.fill_timeout:g}초 안에 체결되지 않아 취소했습니다 "
                               f"({self.unfilled}/{MAX_UNFILLED}). 다시 추적합니다.")
            if self.unfilled >= MAX_UNFILLED:
                raise StopBot(f"트레일링 주문이 연속 {MAX_UNFILLED}번 체결되지 않아 멈춥니다.")
            return

        # 체결됨 — 체결금액을 다시 조회해 장부에 반영한다
        try:
            amount = self.fill_amount(self.history(), live)
        except (MeritzError, requests.RequestException):
            amount = Decimal(0)
        self.orders.pop(side, None)
        self.apply_fill(side, live, amount)
        old_base = self.base
        self.base = self.next_base(side, limit)   # 매수=기준가×(1+매수율), 매도=기준가/(1+매수율)
        self.ext = {"buy": None, "sell": None}
        self.log("체결", f"{'매수' if side == 'buy' else '매도'} 지정가 ${limit} × {live.qty} "
                         f"(주문번호 {live.oder_no}, 체결금액 ${amount if amount > 0 else limit * live.qty:,.2f}) "
                         f"→ {self.ledger_text()}, 기준가 ${old_base} → ${self.base}")
        self.notify()
        if self.fills >= self.max_fills:
            raise StopBot(f"--max-fills({self.max_fills}) 에 도달해 종료합니다.")

    # ======================= 실행 루프 =======================
    def loop_trailing_ws(self) -> None:
        """웹소켓 시세가 올 때마다 바로 판단한다 (폴링 없음)."""
        feed, q = self.feed, self.tick_q
        assert feed is not None and q is not None and self.tick_key is not None
        started = time.monotonic()
        last_notice = started
        while not self.stop_event.is_set():
            feed.check(self.tick_key)
            try:
                price = q.get(timeout=1.0)
            except queue.Empty:
                now = time.monotonic()
                ref = feed.last_tick.get(self.tick_key) or started
                if now - ref >= 60 and now - last_notice >= 60:
                    last_notice = now
                    self.log("시세없음", f"{int(now - ref)}초째 체결 시세가 오지 않습니다 "
                                         f"(장 마감·거래 없음·연결 문제일 수 있음). "
                                         f"연결 상태: {'연결됨' if feed.connected else '끊김'}")
                continue
            self.fired = False
            try:
                self.step_trailing(price)
            except (MeritzError, requests.RequestException) as e:
                self.log("조회오류", str(e))
            if self.fired:
                while True:      # 주문하며 기다린 사이 쌓인 오래된 시세는 버린다
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

    def loop_resting_ws(self) -> None:
        """체결 통보가 오면 즉시, 오지 않아도 --poll 초마다 주문내역을 확인한다."""
        feed = self.feed
        assert feed is not None
        quick = 0            # 통보 직후 주문내역 반영이 늦을 수 있어 짧게 몇 번 더 확인
        warned = False
        last_step = time.monotonic()
        while not self.stop_event.is_set():
            if feed.fatal and not warned:
                warned = True
                self.log("WS중단", f"{feed.fatal} — 체결통보 없이 {self.poll:g}초마다 확인합니다.")
            woke = self.fill_event.wait(1.0)          # 1초마다 깨어나 종료 요청을 확인한다
            now = time.monotonic()
            if woke:
                self.fill_event.clear()
                quick, due = 3, True
            elif quick > 0:
                quick, due = quick - 1, True
            else:
                due = now - last_step >= self.poll
            if not due:
                continue
            last_step = now
            try:
                self.step_resting()
            except (MeritzError, requests.RequestException) as e:
                self.log("조회오류", str(e))

    def loop_polling(self) -> None:
        """--no-ws: 웹소켓 없이 주기적으로 확인 (예전 방식)."""
        while not self.stop_event.is_set():
            if self.stop_event.wait(self.poll):
                return
            try:
                self.step()
            except (MeritzError, requests.RequestException) as e:
                # 조회 실패는 일시적일 수 있다 — 주문 상태를 모른 채 새 주문을 내지 않고 다시 시도한다.
                self.log("조회오류", str(e))

    def step(self) -> None:
        if self.trailing is None:
            self.step_resting()
        else:
            self.step_trailing()

    def run(self, base: Decimal) -> None:
        self.base = base
        if self.trailing is None:
            try:
                self.place_pair()
            except (MeritzError, requests.RequestException) as e:
                raise StopBot(f"최초 주문 중 오류: {e}") from None
        else:
            buy_t, sell_t = target_prices(self.base, self.down, self.up)
            self.log("기준가", f"${self.base} → 매수 목표 ${buy_t} / 매도 목표 ${sell_t} "
                               f"(트레일링 {self.trailing}%, {self.ledger_text()})")
            cb, cs, bn, sn = self.trail_sides(buy_t)
            self.log("추적대상", f"매수 {'가능' if cb else '불가 (' + bn + ')'} · 매도 {'가능' if cs else '불가 (' + sn + ')'} — "
                                 f"{'매수 목표 $' + str(buy_t) + ' 이하' if cb else ''}"
                                 f"{' / ' if cb and cs else ''}"
                                 f"{'매도 목표 $' + str(sell_t) + ' 이상' if cs else ''}"
                                 f"에 닿으면 추적을 시작합니다")
        if self.feed is None:
            self.loop_polling()
        elif self.trailing is not None:
            self.loop_trailing_ws()
        else:
            self.loop_resting_ws()

    def shutdown(self) -> None:
        """걸어 둔 주문을 취소한다 (--keep-orders 면 그대로 둔다). 웹소켓 연결은 건드리지 않는다."""
        if not self.orders:
            return
        if self.keep_orders:
            for live in self.orders.values():
                self.log("유지", f"{live.side} ${live.price} (주문번호 {live.oder_no}, 일자 {live.oder_date}) 를 그대로 둡니다.")
            return
        try:
            rows = self.history()
        except (MeritzError, requests.RequestException):
            rows = []
        for side, live in list(self.orders.items()):
            _, filled, _ = self.inspect(rows, live) if rows else (False, Decimal(0), None)
            if filled >= live.qty:
                self.log("주의", f"{side} 주문 {live.oder_no} 은 이미 체결되었습니다. 보유 수량을 확인하세요 "
                                 f"(장부에는 아직 반영되지 않았습니다).")
                continue
            try:
                self.cancel(live, filled)
                self.orders.pop(side)
            except StopBot as e:
                self.log("정리실패", f"{e} — meritz_us_cancel.py 로 직접 취소하세요.")


# --------------------------------------------------------------------------- #
# 한 종목 명령행 프로그램
# --------------------------------------------------------------------------- #
def _pct(p: argparse.ArgumentParser, text: str, name: str, lo: Decimal, hi: Decimal,
         lo_inclusive: bool = False) -> Decimal:
    """'0.1%' 또는 '0.1' → Decimal(0.1). lo < 값 <= hi 가 아니면 오류."""
    try:
        v = Decimal(str(text).strip().rstrip("%"))
    except InvalidOperation:
        p.error(f"{name} 값 '{text}' 을 숫자로 읽을 수 없습니다. 예: 0.1%")
    if not ((lo <= v if lo_inclusive else lo < v) and v <= hi):
        p.error(f"{name} 은 {lo}% {'이상' if lo_inclusive else '초과'} {hi}% 이하여야 합니다 (받은 값: {text}).")
    return v


def parse_args() -> tuple[argparse.Namespace, Decimal, Decimal]:
    # '-1.0%' 같은 값은 argparse 가 옵션으로 오해하므로 먼저 따로 뽑는다. (먼저 나온 값 = 하락, 다음 = 상승)
    # 단, --trailing-stop +0.1% 처럼 옵션이 가져가는 값은 건드리지 않는다.
    argv = sys.argv[1:]
    pcts, rest = [], []
    for i, t in enumerate(argv):
        prev = argv[i - 1] if i else ""
        (pcts if PCT_RE.match(t) and prev not in VALUE_OPTS else rest).append(t)

    p = argparse.ArgumentParser(
        description="미국 주식 기준가 −x% 매수 / +y% 매도 반복 매매 (실시간 웹소켓)",
        epilog="예) python meritz_us_ladder.py AAPL 1 -1.0% +3.0% --exch NASDAQ --trailing-stop 0.1%",
    )
    p.add_argument("symbol", help="심볼 (예: AAPL)")
    p.add_argument("qty", type=int, help="1회 주문 수량(주)")
    p.add_argument("--exch", required=True, help="거래소 NASDAQ / NYSE / AMEX")
    p.add_argument("--price", help="기준가를 현재가 대신 이 값으로 지정 (예: 100.00)")
    p.add_argument("--trailing-stop", dest="trailing", help="트레일링 스톱 폭 (예: 0.1%%). 지정하면 추적 후 주문, 0 이면 극값을 지나 처음 반대로 움직일 때 주문")
    p.add_argument("--slippage", default="0.1%", help="(사용 안 함 — 트레일링 주문은 목표가로 냅니다. 이전 명령어와의 호환용)")
    p.add_argument("--fill-timeout", type=float, default=30.0, help="트레일링 주문 체결 대기(초, 기본 30)")
    p.add_argument("--position", type=int, default=0, help="이미 보유 중이며 매도해도 되는 수량 (기본 0)")
    p.add_argument("--max-position", type=int, help="보유 한도(주). 기본: 주문수량 × 5")
    p.add_argument("--max-fills", type=int, default=20, help="체결이 이만큼 나면 종료 (기본 20)")
    p.add_argument("--poll", type=float,
                   help="주문내역 확인 주기(초). 웹소켓 사용 시 체결통보가 없을 때의 보조 주기 (기본 10), "
                        "--no-ws 일 때는 고정 5초 / 트레일링 2초")
    p.add_argument("--no-ws", action="store_true", help="웹소켓을 쓰지 않고 REST 로 주기적으로 확인 (예전 방식)")
    p.add_argument("--show-trail", action="store_true",
                   help="트레일링으로 추적 중일 때 시세가 올 때마다 상태(극값·트리거 가격)를 출력")
    p.add_argument("--status-every", type=float,
                   help="추적 시작 전(대기 중) 상태를 출력하는 주기(초). 기본: --show-trail 이면 10, 아니면 출력 안 함. 0 이면 끔")
    p.add_argument("--delayed", action="store_true", help="시작 가격 조회에 실시간 대신 지연 시세 사용")
    p.add_argument("--keep-orders", action="store_true", help="종료할 때 걸어 둔 주문을 취소하지 않음")
    p.add_argument("--yes", action="store_true", help="실제로 실행 (없으면 계획만 출력)")
    a = p.parse_args(rest)

    if len(pcts) != 2:
        p.error("하락률과 상승률을 %로 두 개 주세요. 예: AAPL 1 -1.0% +3.0%")
    down, up = Decimal(pcts[0].rstrip("%")), Decimal(pcts[1].rstrip("%"))
    if not (Decimal(-100) < down < 0):
        p.error("첫 번째 값(하락률)은 -100% 보다 크고 0% 보다 작아야 합니다. 예: -1.0%")
    if up <= 0:
        p.error("두 번째 값(상승률)은 0% 보다 커야 합니다. 예: +3.0%")

    a.symbol = a.symbol.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,10}", a.symbol):
        p.error(f"심볼 '{a.symbol}' 이 올바르지 않습니다.")
    if a.exch.upper() not in EXCHANGES:
        p.error("--exch 는 NASDAQ / NYSE / AMEX 중 하나여야 합니다.")
    a.exch_code = EXCHANGES[a.exch.upper()]
    if a.qty < 1 or a.position < 0 or a.max_fills < 1:
        p.error("수량·보유수량·--max-fills 값을 확인하세요.")
    if a.max_position is None:
        a.max_position = a.qty * 5
    if a.position > a.max_position:
        p.error("--position 이 --max-position 보다 클 수 없습니다.")

    # --price: 기준가 직접 지정
    if a.price is not None:
        try:
            a.price = Decimal(a.price.replace(",", ""))
        except InvalidOperation:
            p.error(f"--price '{a.price}' 을 숫자로 읽을 수 없습니다.")
        if a.price <= 0:
            p.error("--price 는 0보다 커야 합니다.")

    # --trailing-stop / --slippage
    a.trailing = _pct(p, a.trailing, "--trailing-stop", Decimal(0), Decimal(50), lo_inclusive=True) if a.trailing is not None else None
    a.slippage = _pct(p, a.slippage, "--slippage", Decimal(-1), Decimal(5))
    if a.slippage < 0:
        p.error("--slippage 는 0% 이상이어야 합니다.")
    if a.fill_timeout < 5:
        p.error("--fill-timeout 은 5초 이상이어야 합니다.")
    if a.trailing is not None and a.delayed:
        p.error("트레일링은 실시간 시세로만 동작합니다. --delayed 와 함께 쓸 수 없습니다.")

    if a.status_every is not None and a.status_every < 0:
        p.error("--status-every 는 0 이상이어야 합니다.")
    if a.status_every is None:
        a.status_every = 10.0 if a.show_trail else 0.0
    a.ws = not a.no_ws
    if a.poll is None:
        a.poll = (10.0 if a.trailing is None else 2.0) if a.ws else (5.0 if a.trailing is None else 2.0)
    a.poll = max(a.poll, 1.0 if (a.trailing is not None and not a.ws) else 2.0)
    return a, down, up


def main() -> None:
    a, down, up = parse_args()
    a.exch_name = EXCH_NAME[a.exch_code]
    trailing = a.trailing is not None

    if a.ws:
        try:
            import websockets  # noqa: F401
        except ImportError:
            raise SystemExit("웹소켓을 쓰려면 websockets 가 필요합니다: pip install websockets "
                             "(또는 --no-ws 로 REST 확인 방식을 쓰세요)") from None

    # 현재가: --price 가 없으면 필수, 있으면 참고용 (트레일링은 실행 중 시세가 필요하므로 필수)
    print_account()
    current: Decimal | None = None
    try:
        current = get_price(a.symbol, a.exch_code, a.delayed)
        held, sellable = get_holdings(a.symbol)
    except MeritzError as e:
        if a.price is None or trailing or current is not None:
            raise SystemExit(f"실패: {e}") from None
        print(f"[참고] 현재가를 가져오지 못했습니다({e}). --price 로 시작합니다.")
        try:
            held, sellable = get_holdings(a.symbol)
        except MeritzError as e2:
            raise SystemExit(f"실패: {e2}") from None
    if a.position > sellable:
        raise SystemExit(f"--position {a.position} 이 매도가능수량({sellable})보다 큽니다. 시작하지 않습니다.")

    base = a.price if a.price is not None else current
    assert base is not None
    buy_t, sell_t = target_prices(base, down, up)
    if buy_t < 1:
        raise SystemExit(f"매수 목표가 ${buy_t} 로 1달러 미만이라 지원하지 않습니다.")

    tick_key = f"{QUOTE_EXCH[a.exch_code]}@{a.symbol}"
    print(f"\n{a.symbol} ({a.exch_name}) · 1회 {a.qty}주 · "
          f"{'트레일링 ' + str(a.trailing) + '%' if trailing else '고정 지정가'}")
    if a.price is not None:
        print(f"  기준가          ${base}   (--price 로 지정)")
        if current is not None:
            print(f"  현재가(참고)    ${current}   (기준가 대비 {(current / base - 1) * 100:+.2f}%)")
    else:
        print(f"  현재가(기준가)  ${base}")
    label = "목표" if trailing else "지정가"
    print(f"  매수 {label:<5}     ${buy_t}   ({down:+}%)")
    print(f"  매도 {label:<5}     ${sell_t}   ({up:+}%)" +
          ("" if a.position >= a.qty else "   ← 보유 수량이 없어 첫 매수 체결 뒤부터 적용됩니다"))
    if trailing:
        print(f"  트레일링 방식   매수 목표 이하로 내려간 뒤 최저점 대비 +{a.trailing}% 반등하면 매수,")
        print(f"                  매도 목표 이상으로 오른 뒤 최고점 대비 -{a.trailing}% 하락하면 매도")
        print(f"  주문            목표가 지정가 / 체결 대기 {a.fill_timeout:g}초 (미체결이면 취소 후 재추적)")
    print(f"  계좌 보유       {held}주 (매도가능 {sellable}주) · 이 프로그램이 매도할 수량 {a.position}주")
    print(f"  보유 한도       {a.max_position}주 (한도까지 사면 약 ${base * a.max_position:,.2f} 투입)")
    if a.ws:
        what = f"실시간 체결 시세({tick_key}) + 체결통보" if trailing else "체결통보 신호 + 주문내역 확인"
        print(f"  확인 방식       웹소켓 — {what} (보조 확인 {a.poll:g}초)")
    else:
        print(f"  확인 방식       REST 폴링 {a.poll:g}초 (--no-ws)")
    print(f"  종료 조건       체결 {a.max_fills}회")

    # 기준가와 시세가 어긋난 경우 미리 알린다
    if current is not None and a.price is not None:
        if trailing:
            if current <= buy_t:
                print("\n⚠ 현재가가 이미 매수 목표 이하입니다. 시작하자마자 최저점 추적을 시작합니다.")
            if current >= sell_t and a.position >= a.qty:
                print("\n⚠ 현재가가 이미 매도 목표 이상입니다. 시작하자마자 최고점 추적을 시작합니다.")
        else:
            if buy_t >= current:
                print("\n⚠ 매수 지정가가 현재가 이상이라 주문하면 바로 체결될 수 있습니다.")
            if sell_t <= current and a.position >= a.qty:
                print("\n⚠ 매도 지정가가 현재가 이하라 주문하면 바로 체결될 수 있습니다.")

    if not a.yes:
        print("\n[계획만 출력] 주문하지 않았습니다. 실제로 실행하려면 --yes 를 붙이세요.")
        return
    print("\n!! 실제 주문이 나갑니다. 하락이 이어지면 -x% 마다 계속 매수해 한도까지 보유하게 됩니다.")
    if input("진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
        print("실행하지 않고 종료합니다.")
        return

    bot = Ladder(a, down, up)
    feed: Feed | None = None
    try:
        if a.ws:
            # 트레일링은 체결 시세를, 고정 지정가 모드는 체결통보만 구독한다.
            feed = Feed([tick_key] if trailing else [], bot.log)
            bot.attach_feed(feed, tick_key if trailing else None)
            feed.start()
        print(f"\n실행 중입니다. 종료하려면 Ctrl+C (걸어 둔 주문은 "
              f"{'그대로 둡니다' if a.keep_orders else '취소하고 종료합니다'}).\n")
        bot.run(base)
    except KeyboardInterrupt:
        print()
        bot.log("중단", "사용자가 종료했습니다.")
    except StopBot as e:
        bot.log("중단", str(e))
    finally:
        bot.shutdown()
        if feed is not None:
            feed.stop()
        extra = f", 체결통보 {feed.fills_seen}건 수신" if feed is not None else ""
        print(f"\n종료. 이 프로그램 기준 보유 {bot.position}주, 누적 체결 {bot.fills}회{extra}. 로그: {bot.log_path}")
        print("※ 실제 보유 수량은 잔고/주문내역에서 꼭 확인하세요.")


if __name__ == "__main__":
    main()
