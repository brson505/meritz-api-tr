"""메리츠증권 Open API — 파일로 여러 종목 반복 매매 (기준가 -x% 매수 / +y% 매도, 실시간 웹소켓)

  python meritz_us_ladder_file.py symbols.csv           # 파일 검증 + 계획만 출력 (주문 안 함)
  python meritz_us_ladder_file.py symbols.csv --yes     # 실제 실행

파일 형식 (CSV, 첫 줄은 제목)
  종목,수량,매수율,매도율,trailing-stop,기준가격,잔고현금,잔고수량
  SOXL.AMEX,1,-2.0%,+5.0%,0.1%,-,100,0
  TQQQ.NASDAQ,1,-1.0%,+3.0%,0,48.50,0,10

  종목          심볼.거래소 (거래소: NASDAQ / NYSE / AMEX)
  수량          1회 주문 수량(주)
  매수율/매도율  기준가 대비 % (매수율은 음수, 매도율은 양수)
  trailing-stop 0.1% 처럼 주면 트레일링(추적 후 주문), - 이면 고정 지정가를 미리 걸어 둔다,
                0 (0%) 이면 트레일링이되 되돌림 폭 없이, 목표 이상 오르다 내리면 매도·목표 이하 내리다 오르면 매수 주문
  기준가격       - 이면 시작할 때의 현재가, 숫자면 그 값
  잔고현금       이 종목에 쓸 수 있는 현금(달러). 이 안에서만 매수한다
  잔고수량       이 종목에서 매도해도 되는 수량. 이 안에서만 매도한다
  '#' 으로 시작하는 줄과 빈 줄은 무시하고, 파일을 갱신할 때 그대로 둡니다.

트레일링 상태 출력
  --show-trail      추적을 시작한 종목은 시세가 올 때마다 '현재가 · 최저/최고 · 주문 트리거 가격'을 출력
  --status-every N  추적을 시작하기 전(목표에 닿기 전)에는 N초마다 현재가와 목표까지의 거리를 출력
                    (--show-trail 을 주면 기본 10초, 0 이면 끔). 매수/매도가 불가한 쪽은 사유를 함께 보여 줌.

동작
  종목마다 meritz_us_ladder.py 의 매매 엔진이 따로(스레드) 돌고, 웹소켓 연결은 하나를 같이 씁니다.
    고정 지정가: 기준가 −x% 매수 / +y% 매도 주문을 걸어 두고, 한쪽이 체결되면 반대쪽을 취소하고
                새 기준가로 다시 건다.
    트레일링   : 목표에 닿은 뒤 극값에서 trailing-stop 만큼 되돌리면 주문한다.
    새 기준가  : 주문 가격과 무관하게 기존 기준가에서 계산 (두 모드 동일).
                매수 체결이면 '기존 기준가 × (1 + 매수율)' (100, -1% → 99),
                매도 체결이면 '기존 기준가 / (1 + 매수율)' (99, -1% → 100).
  매수/매도가 체결될 때마다 파일의 기준가격·잔고현금·잔고수량을 갱신합니다.
    잔고현금: 매수하면 체결금액만큼 줄고, 매도하면 체결금액만큼 늘어난다 (수수료는 반영하지 않음).
    잔고수량: 매수하면 늘고 매도하면 준다.
  다시 실행하면 갱신된 값에서 이어서 시작합니다 (기준가격이 숫자이므로 현재가로 다시 잡지 않음).

중요
  잔고현금·잔고수량은 이 프로그램의 '장부'입니다. 계좌의 실제 현금·보유 수량과 자동으로 맞춰지지 않으며,
  이 프로그램 밖에서 사고판 것은 반영되지 않습니다. 장부가 계좌보다 크면 주문이 거절될 수 있습니다.
  종목 하나에서 문제가 생기면 그 종목만 멈추고(주문 취소) 나머지는 계속합니다.
  Ctrl+C 를 누르면 걸려 있는 주문을 모두 취소하고 종료합니다 (--keep-orders 면 그대로 둠).
  파일은 시작할 때 '파일이름.bak' 으로 한 번 백업하고, 갱신은 임시 파일에 쓴 뒤 교체하는 방식입니다.

!! 운영 서버로 실제 주문이 나갑니다.

준비
  pip install requests websockets
  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿
  (meritz_us_ladder.py 와 그것이 쓰는 파일들이 같은 폴더에 있어야 합니다)
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

from meritz_us_ladder import (
    _IO,
    EXCH_NAME,
    EXCHANGES,
    QUOTE_EXCH,
    Feed,
    KST,
    Ladder,
    MeritzError,
    StopBot,
    get_all_holdings,
    get_price,
    target_prices,
)

HEADERS = ["종목", "수량", "매수율", "매도율", "trailing-stop", "기준가격", "잔고현금", "잔고수량"]
_PROMPT = threading.Lock()   # 경고 확인 질문이 동시에 여러 개 뜨지 않게


# --------------------------------------------------------------------------- #
# 파일
# --------------------------------------------------------------------------- #
def _norm(h: str) -> str:
    return h.strip().lower().replace("_", "-").replace(" ", "")


@dataclass
class Spec:
    row: int                 # 파일 안 행 인덱스 (갱신할 때 쓴다)
    line: int                # 사람이 보는 줄 번호
    symbol: str
    exch_code: str
    qty: int
    down: Decimal
    up: Decimal
    trailing: Decimal | None
    base: Decimal | None     # None 이면 현재가
    cash: Decimal
    position: int

    @property
    def exch_name(self) -> str:
        return EXCH_NAME[self.exch_code]

    @property
    def tick_key(self) -> str:
        return f"{QUOTE_EXCH[self.exch_code]}@{self.symbol}"


def _fmt_price(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    if "." not in s:
        return s + ".00"
    return s + "0" * max(0, 2 - len(s.split(".")[1]))


class Sheet:
    """CSV 파일을 읽고, 체결로 값이 바뀔 때마다 해당 행의 3개 칸만 고쳐서 안전하게 다시 쓴다."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        for enc in ("utf-8-sig", "cp949"):         # 엑셀(한글 윈도우)에서 저장한 CSV 도 읽는다
            try:
                text = raw.decode(enc)
                self.encoding = enc
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("파일 인코딩을 읽을 수 없습니다 (UTF-8 또는 CP949 로 저장하세요).")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.rows: list[list[str]] = list(csv.reader(text.splitlines()))
        self.lock = threading.Lock()
        self.header_row = -1
        self.col: dict[str, int] = {}
        self._find_header()

    def _find_header(self) -> None:
        wanted = {_norm(h): h for h in HEADERS}
        for i, row in enumerate(self.rows):
            cells = [c.strip() for c in row]
            if not any(cells) or cells[0].startswith("#"):
                continue
            col = {wanted[_norm(c)]: j for j, c in enumerate(cells) if _norm(c) in wanted}
            missing = [h for h in HEADERS if h not in col]
            if missing:
                raise ValueError(f"{i + 1}행: 제목 줄에 다음 칸이 없습니다 → {', '.join(missing)}\n"
                                 f"   제목은 이 순서를 권장합니다: {','.join(HEADERS)}")
            self.header_row, self.col = i, col
            return
        raise ValueError("제목 줄(종목,수량,매수율,…)을 찾지 못했습니다.")

    def _cell(self, row: list[str], name: str) -> str:
        j = self.col[name]
        return row[j].strip() if j < len(row) else ""

    def specs(self) -> list[Spec]:
        """모든 행을 검증해서 Spec 목록을 만든다. 하나라도 틀리면 전부 모아서 알린다."""
        out: list[Spec] = []
        errors: list[str] = []
        seen: dict[str, int] = {}
        for i in range(self.header_row + 1, len(self.rows)):
            row = self.rows[i]
            if not any(c.strip() for c in row) or row[0].strip().startswith("#"):
                continue
            try:
                sp = self._parse_row(i, row)
            except ValueError as e:
                errors.append(f"{i + 1}행: {e}")
                continue
            if sp.symbol in seen:
                errors.append(f"{i + 1}행: {sp.symbol} 이 {seen[sp.symbol]}행에 이미 있습니다 (종목은 한 번만 쓸 수 있습니다).")
                continue
            seen[sp.symbol] = i + 1
            out.append(sp)
        if errors:
            raise ValueError("파일에 오류가 있어 아무것도 시작하지 않았습니다.\n  " + "\n  ".join(errors))
        if not out:
            raise ValueError("처리할 종목이 없습니다.")
        return out

    def _parse_row(self, i: int, row: list[str]) -> Spec:
        probs: list[str] = []

        def dec(name: str, allow: tuple[str, ...] = ()) -> Decimal | None:
            t = self._cell(row, name).replace(",", "").rstrip("%").strip()
            if t in allow:
                return None
            try:
                return Decimal(t)
            except InvalidOperation:
                probs.append(f"{name} '{self._cell(row, name)}' 을 숫자로 읽을 수 없습니다")
                return None

        # 종목: 심볼.거래소  (BRK.B.NYSE 처럼 심볼에 점이 있어도 마지막 점만 나눈다)
        raw = self._cell(row, "종목")
        sym, _, ex = raw.upper().rpartition(".")
        if not sym or ex not in EXCHANGES:
            probs.append(f"종목 '{raw}' — '심볼.거래소' 형식이어야 합니다 (예: SOXL.AMEX, 거래소는 NASDAQ/NYSE/AMEX)")
            sym, ex = raw, "NASDAQ"
        elif not (sym[0].isalpha() and all(c.isalnum() or c in ".-" for c in sym) and len(sym) <= 11):
            probs.append(f"심볼 '{sym}' 이 올바르지 않습니다")

        qty_d = dec("수량")
        qty = 0
        if qty_d is not None:
            if qty_d <= 0 or qty_d != qty_d.to_integral_value():
                probs.append(f"수량 '{self._cell(row, '수량')}' 은 1 이상의 정수여야 합니다")
            else:
                qty = int(qty_d)

        down, up = dec("매수율"), dec("매도율")
        if down is not None and not (Decimal(-100) < down < 0):
            probs.append(f"매수율 '{self._cell(row, '매수율')}' 은 -100% 보다 크고 0% 보다 작아야 합니다 (예: -2.0%)")
        if up is not None and up <= 0:
            probs.append(f"매도율 '{self._cell(row, '매도율')}' 은 0% 보다 커야 합니다 (예: +5.0%)")

        trailing = dec("trailing-stop", allow=("", "-"))
        if trailing is not None and not (0 <= trailing <= 50):
            probs.append(f"trailing-stop '{self._cell(row, 'trailing-stop')}' 은 - (고정 지정가) 또는 0~50% 여야 합니다")

        base = dec("기준가격", allow=("", "-"))
        if base is not None and base <= 0:
            probs.append(f"기준가격 '{self._cell(row, '기준가격')}' 은 0 보다 커야 합니다 (현재가를 쓰려면 -)")

        cash = dec("잔고현금", allow=("",))
        if cash is None:
            cash = Decimal(0)
        elif cash < 0:
            probs.append(f"잔고현금 '{self._cell(row, '잔고현금')}' 은 0 이상이어야 합니다")
        pos_d = dec("잔고수량", allow=("",))
        position = 0
        if pos_d is not None:
            if pos_d < 0 or pos_d != pos_d.to_integral_value():
                probs.append(f"잔고수량 '{self._cell(row, '잔고수량')}' 은 0 이상의 정수여야 합니다")
            else:
                position = int(pos_d)

        if probs:
            raise ValueError("; ".join(probs))
        assert down is not None and up is not None
        return Spec(i, i + 1, sym, EXCHANGES[ex], qty, down, up, trailing, base, cash, position)

    # ---- 갱신 ----------------------------------------------------------------
    def backup(self) -> Path:
        bak = self.path.with_name(self.path.name + ".bak")
        shutil.copy2(self.path, bak)
        return bak

    def update(self, spec: Spec, base: Decimal, cash: Decimal, position: Decimal) -> None:
        """해당 종목 행의 기준가격·잔고현금·잔고수량만 바꿔서 파일을 다시 쓴다 (임시 파일 → 교체)."""
        values = {"기준가격": _fmt_price(base), "잔고현금": f"{cash:.2f}", "잔고수량": str(int(position))}
        with self.lock:
            row = self.rows[spec.row]
            for name, val in values.items():
                j = self.col[name]
                while len(row) <= j:
                    row.append("")
                row[j] = val
            enc = "utf-8-sig" if (self.encoding == "utf-8-sig" and self.bom) else \
                  ("utf-8" if self.encoding == "utf-8-sig" else self.encoding)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with tmp.open("w", encoding=enc, newline="") as f:
                csv.writer(f, lineterminator=self.newline).writerows(self.rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #
def ask(prompt: str) -> str:
    with _PROMPT:
        return input(prompt)


def explain(sp: Spec, current: Decimal | None, base: Decimal, sellable: Decimal | None,
            slippage: Decimal) -> tuple[list[str], bool]:
    """종목 하나의 계획 설명과 경고. 두 번째 값은 '실행 가능한가'."""
    buy_t, sell_t = target_prices(base, sp.down, sp.up)
    mode = f"트레일링 {sp.trailing}%" if sp.trailing is not None else "고정 지정가"
    lines = [f"{sp.symbol} ({sp.exch_name}) · 1회 {sp.qty}주 · {mode}"]
    src = "현재가" if sp.base is None else "파일의 기준가격"
    lines.append(f"    기준가 ${base} ({src})" +
                 ("" if current is None or sp.base is None else
                  f" · 현재가 ${current} (기준가 대비 {(current / base - 1) * 100:+.2f}%)"))
    label = "목표" if sp.trailing is not None else "지정가"
    lines.append(f"    매수 {label} ${buy_t} ({sp.down:+}%) · 매도 {label} ${sell_t} ({sp.up:+}%)")

    est = buy_t * (1 + slippage / 100) if sp.trailing is not None else buy_t
    buyable = int(sp.cash // (est * sp.qty)) * sp.qty if est > 0 else 0
    can_buy, can_sell = buyable >= sp.qty, sp.position >= sp.qty
    lines.append(f"    잔고현금 ${sp.cash:,.2f} → 처음에 매수 {'가능 (약 ' + str(buyable) + '주까지)' if can_buy else '불가'}"
                 f" · 잔고수량 {sp.position}주 → 매도 {'가능' if can_sell else '불가 (매수 체결 후 가능)'}")
    warns: list[str] = []
    if buy_t < 1:
        warns.append("매수 목표가 1달러 미만이라 지원하지 않습니다 — 이 종목은 건너뜁니다.")
        return lines + [f"    ⚠ {w}" for w in warns], False
    if not can_buy and not can_sell:
        warns.append("현금도 수량도 부족해 주문할 수 없습니다 — 이 종목은 건너뜁니다.")
    if sellable is not None and sp.position > sellable:
        warns.append(f"잔고수량 {sp.position}주가 계좌 매도가능수량 {sellable}주보다 큽니다 — 매도 주문이 거절될 수 있습니다.")
    if current is not None and sp.base is not None:
        if sp.trailing is not None:
            if current <= buy_t and can_buy:
                warns.append("현재가가 이미 매수 목표 이하입니다. 시작하자마자 최저점 추적을 시작합니다.")
            if current >= sell_t and can_sell:
                warns.append("현재가가 이미 매도 목표 이상입니다. 시작하자마자 최고점 추적을 시작합니다.")
        else:
            if buy_t >= current and can_buy:
                warns.append("매수 지정가가 현재가 이상이라 주문하면 바로 체결될 수 있습니다.")
            if sell_t <= current and can_sell:
                warns.append("매도 지정가가 현재가 이하라 주문하면 바로 체결될 수 있습니다.")
    return lines + [f"    ⚠ {w}" for w in warns], (can_buy or can_sell)


def main() -> None:
    p = argparse.ArgumentParser(description="CSV 파일로 여러 종목 반복 매매 (실시간 웹소켓)")
    p.add_argument("file", type=Path, help="종목 파일 (CSV)")
    p.add_argument("--slippage", default="0.1", help="트레일링 주문 지정가 여유 %% (기본 0.1)")
    p.add_argument("--fill-timeout", type=float, default=30.0, help="트레일링 주문 체결 대기(초, 기본 30)")
    p.add_argument("--max-fills", type=int, default=20, help="종목별로 체결이 이만큼 나면 그 종목을 멈춤 (기본 20)")
    p.add_argument("--poll", type=float, default=10.0, help="주문내역 보조 확인 주기(초, 기본 10)")
    p.add_argument("--no-ws", action="store_true", help="웹소켓 없이 REST 로 주기적으로 확인")
    p.add_argument("--show-trail", action="store_true",
                   help="트레일링으로 추적 중일 때 시세가 올 때마다 상태(극값·트리거 가격)를 출력")
    p.add_argument("--status-every", type=float,
                   help="추적 시작 전(대기 중) 상태를 출력하는 주기(초). 기본: --show-trail 이면 10, 아니면 출력 안 함. 0 이면 끔")
    p.add_argument("--keep-orders", action="store_true", help="종료할 때 걸어 둔 주문을 취소하지 않음")
    p.add_argument("--yes", action="store_true", help="실제로 실행 (없으면 계획만 출력)")
    a = p.parse_args()
    try:
        slippage = Decimal(a.slippage.rstrip("%"))
    except InvalidOperation:
        p.error("--slippage 값을 숫자로 읽을 수 없습니다.")
    if not (0 <= slippage <= 5) or a.fill_timeout < 5 or a.max_fills < 1:
        p.error("--slippage(0~5), --fill-timeout(5초 이상), --max-fills(1 이상) 값을 확인하세요.")
    if a.status_every is not None and a.status_every < 0:
        p.error("--status-every 는 0 이상이어야 합니다.")
    if a.status_every is None:
        a.status_every = 10.0 if a.show_trail else 0.0

    if not a.no_ws:
        try:
            import websockets  # noqa: F401
        except ImportError:
            raise SystemExit("웹소켓을 쓰려면 websockets 가 필요합니다: pip install websockets "
                             "(또는 --no-ws)") from None

    try:
        sheet = Sheet(a.file)
        specs = sheet.specs()
    except (OSError, ValueError) as e:
        raise SystemExit(str(e)) from None

    # ---- 시세·잔고 조회 (조회만 한다) ----------------------------------------------
    try:
        holdings = get_all_holdings()
    except MeritzError as e:
        print(f"[참고] 계좌 보유 수량을 조회하지 못했습니다({e}). 매도가능수량 점검 없이 진행합니다.")
        holdings = None
    plans: list[tuple[Spec, Decimal, bool]] = []
    print(f"\n{a.file} — 종목 {len(specs)}개")
    for sp in specs:
        current: Decimal | None = None
        try:
            current = get_price(sp.symbol, sp.exch_code)
        except MeritzError as e:
            if sp.base is None or sp.trailing is not None:
                print(f"\n{sp.symbol} ({sp.exch_name}) — 시세를 가져오지 못해 건너뜁니다: {e}")
                continue
            print(f"\n[참고] {sp.symbol} 현재가를 가져오지 못했습니다({e}). 파일의 기준가격으로 진행합니다.")
        base = sp.base if sp.base is not None else current
        assert base is not None
        sellable = None if holdings is None else holdings.get(sp.symbol, (Decimal(0), Decimal(0)))[1]
        lines, ok = explain(sp, current, base, sellable, slippage)
        print("\n" + "\n".join(lines))
        if ok:
            plans.append((sp, base, True))
        time.sleep(0.15)               # 조회 초당 10건 제한 여유

    if not plans:
        raise SystemExit("\n실행할 수 있는 종목이 없습니다.")
    print(f"\n실행 대상 {len(plans)}개 · 종목별 최대 체결 {a.max_fills}회 · "
          f"확인 방식 {'REST 폴링' if a.no_ws else '웹소켓(시세·체결통보) + 보조 확인 ' + str(a.poll) + '초'}")
    print("체결될 때마다 이 파일의 기준가격·잔고현금·잔고수량이 갱신됩니다.")
    if a.show_trail or a.status_every > 0:
        print(f"트레일링 상태 출력: 추적 중이면 시세마다{'' if a.show_trail else ' (꺼짐)'}, "
              f"대기 중이면 {a.status_every:g}초마다{'' if a.status_every > 0 else ' (꺼짐)'}")

    if not a.yes:
        print("\n[계획만 출력] 주문하지 않았습니다. 실제로 실행하려면 --yes 를 붙이세요.")
        return
    print("\n!! 실제 주문이 나갑니다. 잔고현금·잔고수량은 이 프로그램의 장부이며 계좌와 자동으로 맞춰지지 않습니다.")
    if input("진행하려면 yes 를 입력하세요: ").strip().lower() != "yes":
        print("실행하지 않고 종료합니다.")
        return

    bak = sheet.backup()
    print(f"백업: {bak}")

    # ---- 종목마다 엔진 하나 + 스레드 하나, 웹소켓 연결은 공유 -----------------------------
    stop = threading.Event()
    bots: list[tuple[Spec, Ladder]] = []

    def make_updater(sp: Spec):
        def on_update(bot: Ladder) -> None:
            assert bot.cash is not None
            sheet.update(sp, bot.base, bot.cash, bot.position)
        return on_update

    for sp, base, _ in plans:
        ns = SimpleNamespace(
            symbol=sp.symbol, exch_code=sp.exch_code, qty=sp.qty, position=sp.position, max_position=None,
            max_fills=a.max_fills, poll=a.poll if not a.no_ws else max(a.poll, 2.0),
            keep_orders=a.keep_orders, trailing=sp.trailing, slippage=slippage, fill_timeout=a.fill_timeout,
            cash=sp.cash, on_update=make_updater(sp), ask=ask, stop_event=stop,
            show_trail=a.show_trail, status_every=a.status_every,
        )
        bots.append((sp, Ladder(ns, sp.down, sp.up)))

    feed: Feed | None = None
    if not a.no_ws:
        # 트레일링 종목은 체결 시세를 구독하고, 모든 종목이 체결통보 신호를 받는다.
        def ws_log(event: str, msg: str = "", **_kw) -> None:
            with _IO:
                head = "" if event == "WS" else f"{event:<8} "
                print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {'WS':<6} {head}{msg}")

        feed = Feed([sp.tick_key for sp, _ in bots if sp.trailing is not None], ws_log)
        for sp, bot in bots:
            bot.attach_feed(feed, sp.tick_key if sp.trailing is not None else None)
        feed.start()

    bases = {sp.symbol: base for sp, base, _ in plans}

    def worker(sp: Spec, bot: Ladder) -> None:
        try:
            bot.run(bases[sp.symbol])
        except StopBot as e:
            bot.log("중단", str(e))
        except Exception as e:                       # noqa: BLE001 — 한 종목의 예외가 다른 종목을 멈추지 않게
            bot.log("오류", f"{type(e).__name__}: {e}")
        finally:
            try:
                bot.shutdown()
            except Exception as e:                   # noqa: BLE001
                bot.log("정리실패", f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(sp, bot), name=sp.symbol, daemon=True) for sp, bot in bots]
    print(f"\n실행 중입니다. 종료하려면 Ctrl+C (걸어 둔 주문은 "
          f"{'그대로 둡니다' if a.keep_orders else '모두 취소하고 종료합니다'}).\n")
    for t in threads:
        t.start()
        time.sleep(0.3)              # 시작할 때 주문이 한꺼번에 몰리지 않게

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
        print("\n모든 종목이 종료되었습니다.")
    except KeyboardInterrupt:
        print("\n종료 중… 걸려 있는 주문을 취소합니다 (다시 Ctrl+C 하면 기다리지 않고 끝냅니다).")
        stop.set()
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("강제 종료합니다 — 걸려 있는 주문이 남아 있을 수 있으니 meritz_us_cancel.py 로 확인하세요.")
    finally:
        stop.set()
        if feed is not None:
            feed.stop()

    print("\n종료 상태 (파일에도 같은 값이 저장되어 있습니다)")
    for sp, bot in bots:
        base = bot.base if bot.base > 0 else bases[sp.symbol]
        print(f"  {sp.symbol:<6} 기준가격 ${_fmt_price(base)} · 잔고현금 ${bot.cash:,.2f} · 잔고수량 {int(bot.position)} · "
              f"체결 {bot.fills}회 · 로그 {bot.log_path}")
    if feed is not None:
        print(f"  체결통보 {feed.fills_seen}건 수신")
    print("※ 실제 계좌의 현금·보유 수량은 잔고/주문내역에서 꼭 확인하세요.")


if __name__ == "__main__":
    main()
