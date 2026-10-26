"""Stage-one virtual accounts and simulated fills. No brokerage interfaces."""
from __future__ import annotations
import os, re, sqlite3, stat
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

SYMBOL = re.compile(r'^(?:[A-Z][A-Z0-9.]{0,11}|[0-9]{6})$')
ISA_ETF_ALLOWED = frozenset({'069500', '360750'})
KINDS = ('GENERAL_OVERSEAS','GENERAL_DOMESTIC','ISA_SIMULATED')

class Portfolios:
    def __init__(self, location: Path):
        self.path = Path(location)
        if self.path.is_symlink() or not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise ValueError('안전하지 않은 경로')

    def _db(self):
        if self.path.is_symlink(): raise ValueError('저장 경로 검사 실패')
        if self.path.exists():
            s=self.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode) or (os.name=='posix' and (s.st_uid!=os.getuid() or s.st_mode & 0o077)):
                raise ValueError('원장 권한 검사 실패')
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        if os.name=='posix': os.chmod(self.path,0o600)
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS stage1_events (owner TEXT NOT NULL,event_key TEXT NOT NULL,kind TEXT NOT NULL,amount_krw INTEGER NOT NULL,amount_usd TEXT NOT NULL,rate TEXT,created_utc TEXT NOT NULL,PRIMARY KEY(owner,event_key))')
        db.execute('''CREATE TABLE IF NOT EXISTS portfolios (owner TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('GENERAL_OVERSEAS','GENERAL_DOMESTIC','ISA_SIMULATED')), created_at TEXT NOT NULL, PRIMARY KEY(owner,id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS portfolio_transfers (owner TEXT NOT NULL, request_id TEXT NOT NULL, source TEXT NOT NULL, destination TEXT NOT NULL, amount_krw INTEGER NOT NULL CHECK(amount_krw>0), created_at TEXT NOT NULL, PRIMARY KEY(owner,request_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS virtual_orders (owner TEXT NOT NULL, portfolio TEXT NOT NULL, request_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY','SELL')), quantity INTEGER NOT NULL CHECK(quantity>0), price TEXT NOT NULL, currency TEXT NOT NULL, source TEXT NOT NULL, as_of TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(owner,portfolio,request_id))''')
        # Existing ledgers are snapshotted before adding the v1 extension tables.
        # All previously recorded deposits, transfers and fills are untouched.
        try:
            from .stage1_schema import ensure_schema
            ensure_schema(db, self.path)
        except Exception:
            db.close()
            raise
        return db

    @staticmethod
    def _receipt(db, owner, portfolio, order_key, quote, rate=None):
        """Capture only verified trade provenance; unknown costs remain NULL."""
        db.execute("""INSERT OR IGNORE INTO stage1_trade_metadata
           (owner,portfolio,order_key,quote_as_of,foreign_exchange_rate,
            fee_amount,tax_amount,costs_currency,source,observed_at_utc,created_at_utc)
           VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,?)""",
           (owner, portfolio, order_key, quote.get('as_of'), rate,
            quote['source'],datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _assert_not_paused(db,owner):
        table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage1_trade_control'").fetchone()
        if table:
            row=db.execute("SELECT paused FROM stage1_trade_control WHERE owner=?",(owner,)).fetchone()
            if row and row[0]:
                raise ValueError('긴급 중지 중에는 가상 주문을 기록할 수 없습니다.')

    @staticmethod
    def _ensure(db,owner):
        db.execute('INSERT OR IGNORE INTO portfolios VALUES(?,?,?,?,?)',(owner,'default','기본 가상계좌','GENERAL_OVERSEAS',datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _money(db, owner, portfolio):
        base_krw=0; base_usd=Decimal('0')
        if portfolio=='default':
            row=db.execute('SELECT COALESCE(SUM(amount_krw),0) FROM stage1_events WHERE owner=?',(owner,)).fetchone()
            base_krw=int(row[0]) if row else 0
            row=db.execute('SELECT amount_usd FROM stage1_events WHERE owner=?',(owner,)).fetchall()
            base_usd=sum((Decimal(r[0]) for r in row),Decimal('0'))
        out=db.execute('SELECT COALESCE(SUM(amount_krw),0) FROM portfolio_transfers WHERE owner=? AND source=?',(owner,portfolio)).fetchone()[0]
        incoming=db.execute('SELECT COALESCE(SUM(amount_krw),0) FROM portfolio_transfers WHERE owner=? AND destination=?',(owner,portfolio)).fetchone()[0]
        krw=Decimal(base_krw+incoming-out); usd=base_usd; holdings={}
        orders=db.execute('SELECT symbol,side,quantity,price,currency FROM virtual_orders WHERE owner=? AND portfolio=? ORDER BY created_at,request_id',(owner,portfolio)).fetchall()
        for symbol,side,quantity,price,currency in orders:
            holdings[symbol]=holdings.get(symbol,0)+quantity*(1 if side=='BUY' else -1)
            delta=Decimal(price)*quantity*(1 if side=='SELL' else -1)
            if currency=='KRW': krw+=delta
            else: usd+=delta
        return krw,usd,holdings,len(orders)

    def list(self,owner):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._ensure(db,owner)
            accounts=db.execute("""SELECT id,name,kind FROM portfolios WHERE owner=? ORDER BY CASE id WHEN 'default' THEN 0 WHEN 'kr-base' THEN 1 WHEN 'isa-base' THEN 2 ELSE 3 END,created_at,id""",(owner,)).fetchall()
            result=[]
            for ident,name,kind in accounts:
                krw,usd,holdings,count=self._money(db,owner,ident)
                result.append({'id':ident,'name':name,'kind':kind,'krw_cash':format(krw,'f'),'usd_cash':format(usd,'f'),'holdings':[{'symbol':sym,'quantity':qty} for sym,qty in sorted(holdings.items()) if qty], 'trades':count})
            db.execute('COMMIT')
        return {'accounts':result,'live_orders_enabled':False,'paper_orders_enabled':True}

    def create(self,owner,name,kind):
        if not isinstance(name,str) or not 1<=len(name.strip())<=40 or any(ord(c)<32 for c in name) or kind not in KINDS:
            raise ValueError('계좌 정보 오류')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._ensure(db,owner)
            if db.execute('SELECT COUNT(*) FROM portfolios WHERE owner=?',(owner,)).fetchone()[0]>=10: raise ValueError('계좌 한도 초과')
            db.execute('INSERT INTO portfolios VALUES(?,?,?,?,?)',(owner,uuid4().hex,name.strip(),kind,datetime.now(timezone.utc).isoformat()))
            db.execute('COMMIT')
        return self.list(owner)

    def transfer(self,owner,source,destination,amount,request_id):
        if not isinstance(amount,int) or isinstance(amount,bool) or not 1<=amount<=100_000_000 or source==destination or not valid_key(request_id):raise ValueError('이체 입력 오류')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._ensure(db,owner)
            for ident in (source,destination):
                if not db.execute('SELECT 1 FROM portfolios WHERE owner=? AND id=?',(owner,ident)).fetchone(): raise ValueError('계좌 없음')
            prior=db.execute('SELECT source,destination,amount_krw FROM portfolio_transfers WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()
            if prior:
                if prior!=(source,destination,amount): raise ValueError('요청 충돌')
            else:
                cash,_,_=self._money(db,owner,source)[:3]
                if cash<amount: raise ValueError('가상 잔고 부족')
                db.execute('INSERT INTO portfolio_transfers VALUES(?,?,?,?,?,?)',(owner,request_id,source,destination,amount,datetime.now(timezone.utc).isoformat()))
            db.execute('COMMIT')
        return self.list(owner)

    def auto_buy_usd_with_fx(self, owner, symbol, quote, rate, request_id):
        """Atomically convert virtual KRW and buy one USD share; never strand FX."""
        from decimal import ROUND_UP, ROUND_DOWN
        if not valid_key(request_id) or not SYMBOL.fullmatch(symbol):
            raise ValueError('요청 식별자 오류')
        if quote.get('symbol') != symbol or quote.get('tradeable') is not True or quote.get('status') != 'ok' or quote.get('currency') != 'USD':
            raise ValueError('미검증 시세')
        price = Decimal(str(quote['price']))
        fx = Decimal(str(rate))
        if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -4 or not fx.is_finite() or not 100 <= fx <= 10000 or fx.as_tuple().exponent < -4:
            raise ValueError('가격·환율 형식 오류')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._ensure(db, owner)
            self._assert_not_paused(db,owner)
            kind = db.execute("SELECT kind FROM portfolios WHERE owner=? AND id='default'", (owner,)).fetchone()
            if not kind or kind[0] != 'GENERAL_OVERSEAS':
                raise ValueError('해외 가상계좌 없음')
            prior = db.execute("SELECT symbol,side,quantity FROM virtual_orders WHERE owner=? AND portfolio='default' AND request_id=?", (owner,request_id)).fetchone()
            if prior:
                if prior != (symbol, 'BUY', 1):
                    raise ValueError('중복키 충돌')
                db.execute('COMMIT')
                return {'status': 'already_executed', 'accounts': []}
            cash_krw,cash_usd,_,_ = self._money(db,owner,'default')
            gap = max(Decimal('0'), price-cash_usd)
            amount = 0
            if gap:
                # Ensure 2-decimal USD received covers the required share cost.
                amount = int((gap*fx).to_integral_value(rounding=ROUND_UP))
                while (Decimal(amount)/fx).quantize(Decimal('0.01'), rounding=ROUND_DOWN) < gap:
                    amount += 1
                if Decimal(amount)>cash_krw or amount>10_000_000:
                    raise ValueError('원화 부족')
                received=(Decimal(amount)/fx).quantize(Decimal('0.01'),rounding=ROUND_DOWN)
                db.execute('INSERT INTO stage1_events VALUES (?,?,?,?,?,?,?)',
                    (owner,request_id+':fx','virtual_fx',-amount,format(received,'f'),format(fx,'f'),datetime.now(timezone.utc).isoformat()))
            db.execute('INSERT INTO virtual_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (owner,'default',request_id,symbol,'BUY',1,format(price,'f'),'USD',
                quote['source'],quote['as_of'],datetime.now(timezone.utc).isoformat()))
            self._receipt(db,owner,'default',request_id,quote,format(fx,'f'))
            db.execute('COMMIT')
        return {'status': 'executed', 'accounts': self.list(owner)['accounts']}

    def orders(self,owner,portfolio,limit=100):
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100000:
            raise ValueError('거래내역 조회 한도 오류')
        with self._db() as db:
            if not db.execute('SELECT 1 FROM portfolios WHERE owner=? AND id=?',(owner,portfolio)).fetchone(): raise ValueError('계좌 없음')
            rows=db.execute('SELECT request_id,symbol,side,quantity,price,currency,source,as_of,created_at FROM virtual_orders WHERE owner=? AND portfolio=? ORDER BY created_at DESC,request_id DESC LIMIT ?',(owner,portfolio,limit)).fetchall()
        return {'orders':[dict(zip(('id','symbol','side','quantity','price','currency','source','as_of','timestamp'),r)) for r in rows]}

    def trade(self,owner,portfolio,symbol,side,quantity,request_id,quote, *,
              manual_approval_krw=None, manual_fx_krw_per_usd=None,
              verified_turnover_session_date=None):
        if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol) or side not in ('BUY','SELL') or not isinstance(quantity,int) or isinstance(quantity,bool) or not 1<=quantity<=100000 or not valid_key(request_id):raise ValueError('주문 입력 오류')
        currency=quote['currency'];price=Decimal(quote['price'])
        if currency not in ('USD','KRW') or not price.is_finite() or price<=0 or price.as_tuple().exponent < -4: raise ValueError('가격 검증 오류')
        if quote['symbol']!=symbol or quote.get('status')!='ok' or quote.get('tradeable') is not True: raise ValueError('검증되지 않은 시세')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._ensure(db,owner)
            self._assert_not_paused(db,owner)
            row=db.execute('SELECT kind FROM portfolios WHERE owner=? AND id=?',(owner,portfolio)).fetchone()
            if not row: raise ValueError('계좌 없음')
            if (currency=='USD' and row[0]!='GENERAL_OVERSEAS') or (currency=='KRW' and row[0]=='GENERAL_OVERSEAS'):
                raise ValueError('계좌 상품 제한')
            if currency=='KRW' and not re.fullmatch(r'[0-9]{6}',symbol):
                raise ValueError('국내 6자리 종목코드 필요')
            if row[0]=='ISA_SIMULATED' and symbol not in ISA_ETF_ALLOWED:
                raise ValueError('모의 ISA는 검증된 국내 상장 ETF만 허용')

            prior=db.execute('SELECT symbol,side,quantity,price,currency FROM virtual_orders WHERE owner=? AND portfolio=? AND request_id=?',(owner,portfolio,request_id)).fetchone()
            if prior:
                if prior!=(symbol,side,quantity,format(price,'f'),currency): raise ValueError('요청 충돌')
                if verified_turnover_session_date is not None:
                    # Existing fills must be represented by the same-session
                    # reservation claim, or fail closed on historic orphans.
                    from .stage1_turnover_reservations import _coverage, _day
                    _coverage(db,owner,_day(verified_turnover_session_date))
                    matched=db.execute("""SELECT state FROM stage1_turnover_claims
                        WHERE owner=? AND request_id=? AND session_date=?""",
                        (owner,request_id,verified_turnover_session_date)).fetchone()
                    if not matched or matched[0]!='FILLED':
                        raise ValueError('UNRECONCILED_PAPER_FILL')
            else:
                if verified_turnover_session_date is not None and manual_approval_krw is None:
                    raise ValueError('VERIFIED_TURNOVER_NOTIONAL_REQUIRED')
                if manual_approval_krw is not None:
                    # HTTP approval preflight is a UX optimization, not a
                    # security boundary. Lock the SQLite writer and re-check
                    # approval+expiry+amount before the immutable fill insert.
                    from .stage1_controls import schema as approval_schema
                    approval_schema(db)
                    supplied=Decimal(str(manual_approval_krw))
                    if not supplied.is_finite() or supplied<=0:
                        raise ValueError('승인 금액 검증 오류')
                    if currency=='USD':
                        if manual_fx_krw_per_usd is None:
                            raise ValueError('환율 누락')
                        fx=Decimal(str(manual_fx_krw_per_usd))
                        if not fx.is_finite() or not Decimal('100')<=fx<=Decimal('10000'):
                            raise ValueError('환율 검증 오류')
                        expected=price*quantity*fx
                    else:
                        if manual_fx_krw_per_usd is not None:
                            raise ValueError('원화 주문의 불필요한 환율')
                        expected=price*quantity
                    if supplied!=expected:
                        raise ValueError('승인 금액과 주문 시세 불일치')
                    approval=db.execute(
                        'SELECT account,symbol,side,quantity,amount_krw,status,expires_at '
                        'FROM stage1_approvals WHERE owner=? AND request_id=?',
                        (owner,request_id)
                    ).fetchone()
                    if approval is not None:
                        from .stage1_controls import now_utc
                        if (approval[:4]!=(portfolio,symbol,side,quantity) or
                            approval[5]!='APPROVED' or
                            approval[6]<=now_utc().isoformat() or
                            supplied>Decimal(approval[4])):
                            raise ValueError('승인 요청 불일치 또는 만료')
                    elif supplied>=Decimal('300000'):
                        raise ValueError('30만원 이상 승인 필요')
                krw,usd,holdings,_=self._money(db,owner,portfolio)
                if side=='BUY' and (usd if currency=='USD' else krw)<price*quantity: raise ValueError('현금 부족')
                if side=='SELL' and holdings.get(symbol,0)<quantity: raise ValueError('보유수량 부족')
                if verified_turnover_session_date is not None:
                    # The opening NAV/provenance must already exist in an
                    # immutable session row from an independent verifier.
                    # Reserve BUY+SELL gross across ALL owner accounts before
                    # any simulated fill, using THIS BEGIN IMMEDIATE lock.
                    from .stage1_turnover_reservations import reserve
                    reservation=reserve(db,owner=owner,
                        session_date=verified_turnover_session_date,
                        request_id=request_id,account=portfolio,symbol=symbol,side=side,
                        quantity=quantity,gross_krw=manual_approval_krw,
                        now_utc=datetime.now(timezone.utc).isoformat())
                    # After a dropped reply, an old RESERVED/PENDING/UNKNOWN
                    # request is uncertain until explicitly reconciled.
                    # An already-reserved ID is NEVER permission to fill.
                    if reservation["status"] != "RESERVED":
                        raise ValueError("RESERVATION_RECONCILIATION_REQUIRED")
                db.execute('INSERT INTO virtual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)',(owner,portfolio,request_id,symbol,side,quantity,format(price,'f'),currency,quote['source'],quote['as_of'],datetime.now(timezone.utc).isoformat()))
                self._receipt(db,owner,portfolio,request_id,quote)
                if verified_turnover_session_date is not None:
                    from .stage1_turnover_reservations import mark_filled
                    mark_filled(db,owner,request_id,datetime.now(timezone.utc).isoformat())
            db.execute('COMMIT')
        return self.list(owner)

def valid_key(value):
    return isinstance(value,str) and 8<=len(value)<=100 and all(ch.isascii() and (ch.isalnum() or ch in '_:-') for ch in value)
