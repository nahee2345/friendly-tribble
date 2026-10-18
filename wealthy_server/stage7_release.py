"""Stage 7 conservative, read-only acceptance checks for the simulated ledger.

This is NOT a database migration or a repair utility. The evaluator operates
inside one consistent SQLite read transaction and never changes user rows.
A blocking finding prohibits new unattended simulated fills. For missing
external market data, the execution engine independently fails closed.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

RELEASE_AUDIT="stage7-readonly-audit-v1"
CORE=("stage1_events","portfolios","portfolio_transfers","virtual_orders",
      "stage1_schema_registry")
MAX_ROWS=50000
ERRORS_LIMIT=35
REQUIRED_CURRENCY={"GENERAL_OVERSEAS":"USD",
                   "GENERAL_DOMESTIC":"KRW","ISA_SIMULATED":"KRW"}
VALID_ORDER=re.compile(r"^(?:[A-Z][A-Z0-9.]{0,11}|[0-9]{6})$")
ISSUE_CODE=re.compile(r"^[A-Z][A-Z0-9_]{2,90}$")


def _dec(value,*,negative=True):
    if isinstance(value,bool) or value is None:
        raise ValueError("nondecimal ledger value")
    try:
        n=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):
        raise ValueError("nondecimal ledger value") from None
    if not n.is_finite() or abs(n)>Decimal("100000000000000") or (not negative and n<0):
        raise ValueError("out-of-bounds ledger value")
    return n


def _timestamp(value,now):
    if not isinstance(value,str):raise ValueError("invalid ledger timestamp")
    try:
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        utc=parsed.astimezone(timezone.utc)
        if utc>now+timedelta(minutes=5):
            raise ValueError("future ledger timestamp")
        return utc
    except (TypeError,OverflowError):
        raise ValueError("invalid ledger timestamp") from None


def _path_checked(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.parent.is_symlink():
        raise ValueError("unsafe or missing ledger file")
    mode=p.stat(follow_symlinks=False)
    if not stat.S_ISREG(mode.st_mode) or (os.name=="posix" and
          (mode.st_uid!=os.getuid() or mode.st_mode & 0o077)):
        raise ValueError("unsafe ledger file permissions")
    return p


def _readonly(path):
    p=_path_checked(path)
    db=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=10,
                       isolation_level=None)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("BEGIN")
    except Exception:
        db.close()
        raise
    return db


def audit_paper_ledger(owner,portfolios,*,now=None):
    """Owner-scoped bounded reconciliation; never repairs, backfills or trades.

    Source DB is opened read-only and is not passed to Portfolios._db()
    (which runs schema migration / journaling in write-capable mode).
    """
    now=now or datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.tzinfo is None:
        raise ValueError("timezone-aware audit clock required")
    now=now.astimezone(timezone.utc)
    if not isinstance(owner,str) or not 1<=len(owner)<=200:
        raise ValueError("invalid owner")
    findings=[]
    counts={}
    def issue(code,summary,*,blocking=True):
        if not ISSUE_CODE.fullmatch(code):raise ValueError("invalid issue code")
        if len(findings)<ERRORS_LIMIT:
            findings.append({"code":code,"detail":summary,
                             "blocking":blocking})
    try:
        with closing(_readonly(portfolios.path)) as db:
            checked=db.execute("PRAGMA quick_check(1)").fetchone()
            if not checked or checked[0]!="ok":
                issue("SQLITE_QUICK_CHECK_FAILED",
                      "SQLite 원장 무결성 검사 실패")
            tables={x[0] for x in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in CORE:
                if table not in tables:
                    issue("MISSING_TABLE_"+table.upper(),
                          "필수 원장 테이블 누락: "+table)
            # Core audit must not emit an exception hiding an unsafe table.
            if not all(table in tables for table in CORE):
                return _response(findings,counts,now)
            rows=db.execute("""SELECT schema_version FROM stage1_schema_registry
                WHERE id=1""").fetchone()
            if not rows or rows[0]!=1:
                issue("UNSUPPORTED_SCHEMA","지원하지 않는 Stage1 원장 버전")
            accounts={}
            accts=db.execute("""SELECT id,kind FROM portfolios WHERE owner=?
                ORDER BY id LIMIT ?""",(owner,MAX_ROWS+1)).fetchall()
            if len(accts)>10:
                issue("ACCOUNT_LIMIT","가상 계좌 10개 초과")
            for acc,kind in accts:
                if acc in accounts or kind not in REQUIRED_CURRENCY:
                    issue("INVALID_ACCOUNT","계좌 중복/유형 오류")
                accounts[acc]=kind
            counts["accounts"]=len(accounts)
            if not accounts:
                issue("NO_SIMULATED_ACCOUNTS","사용자 가상계좌가 초기화되지 않음")
            else:
                for name,kind in (("default","GENERAL_OVERSEAS"),
                                  ("kr-base","GENERAL_DOMESTIC"),
                                  ("isa-base","ISA_SIMULATED")):
                    if accounts.get(name)!=kind:
                        issue("DEFAULT_ACCOUNT_MISMATCH",name+" 가상계좌/유형 오류")
            control=(db.execute("""SELECT paused FROM stage1_trade_control
                WHERE owner=?""",(owner,)).fetchone()
                if "stage1_trade_control" in tables else None)
            counts["paused"]=bool(control[0]) if control else True
            balances={a:{"KRW":Decimal(0),"USD":Decimal(0)} for a in accounts}
            held=defaultdict(int)
            events=db.execute("""SELECT event_key,kind,amount_krw,amount_usd,
                rate,created_utc FROM stage1_events WHERE owner=?
                ORDER BY created_utc,event_key LIMIT ?""",(owner,MAX_ROWS+1)).fetchall()
            counts["events"]=len(events)
            if len(events)>MAX_ROWS:
                issue("EVENT_SCAN_LIMIT","입금·환전 기록 검증 상한 초과")
            seen_deposits=set()
            for key,kind,krw,usd,fx,created in events[:MAX_ROWS]:
                try:
                    _timestamp(created,now)
                    amount=_dec(krw);usd_amt=_dec(usd)
                    if kind=="deposit":
                        if amount<=0 or usd_amt!=0 or fx is not None:
                            raise ValueError("malformed deposit")
                        seen_deposits.add(key)
                    elif kind=="virtual_fx":
                        rate=_dec(fx,negative=False)
                        if amount>=0 or usd_amt<=0 or not 100<=rate<=10000:
                            raise ValueError("malformed simulated exchange")
                        if abs(abs(amount)-usd_amt*rate)>rate*Decimal("0.03")+Decimal("2"):
                            raise ValueError("exchange not conserved within rounding bound")
                    else:
                        raise ValueError("unrecognized funding event")
                    if "default" not in balances:raise ValueError("default account missing")
                    balances["default"]["KRW"]+=amount
                    balances["default"]["USD"]+=usd_amt
                except (ValueError,TypeError):
                    issue("INVALID_FUNDING_EVENT","가상 입금·환전 값/기준시각 오류")
                    break
            transfers=db.execute("""SELECT request_id,source,destination,amount_krw,
                created_at FROM portfolio_transfers WHERE owner=?
                ORDER BY created_at,request_id LIMIT ?""",(owner,MAX_ROWS+1)).fetchall()
            counts["transfers"]=len(transfers)
            if len(transfers)>MAX_ROWS:
                issue("TRANSFER_SCAN_LIMIT","가상 이체 기록 검증 상한 초과")
            transfer_map={}
            for key,source,destination,krw,created in transfers[:MAX_ROWS]:
                try:
                    _timestamp(created,now)
                    amount=_dec(krw,negative=False)
                    if amount<=0 or source==destination or source not in accounts or destination not in accounts:
                        raise ValueError("invalid internal transfer")
                    balances[source]["KRW"]-=amount
                    balances[destination]["KRW"]+=amount
                    transfer_map[key]=(source,destination,amount)
                except (TypeError,ValueError):
                    issue("INVALID_TRANSFER","가상 계좌 이체가 정상 계좌/금액과 일치하지 않음")
                    break
            orders=db.execute("""SELECT portfolio,request_id,symbol,side,
                quantity,price,currency,source,as_of,created_at
                FROM virtual_orders WHERE owner=? ORDER BY created_at,request_id
                LIMIT ?""",(owner,MAX_ROWS+1)).fetchall()
            counts["orders"]=len(orders)
            if len(orders)>MAX_ROWS:
                issue("ORDER_SCAN_LIMIT","가상 주문 원장 검증 상한 초과")
            executed={}
            from .stage1_portfolios import ISA_ETF_ALLOWED
            for acc,key,sym,side,qty,price,cur,source,as_of,created in orders[:MAX_ROWS]:
                try:
                    _timestamp(created,now)
                    when=datetime.strptime(as_of,"%Y-%m-%d").date()
                    if when>now.date()+timedelta(days=1):raise ValueError("future price date")
                    if (acc not in accounts or cur not in ("USD","KRW")
                        or cur!=REQUIRED_CURRENCY[accounts[acc]]
                        or not isinstance(sym,str) or not VALID_ORDER.fullmatch(sym)
                        or (cur=="KRW" and not sym.isdigit())
                        or (cur=="USD" and sym.isdigit())
                        or (accounts[acc]=="ISA_SIMULATED" and sym not in ISA_ETF_ALLOWED)
                        or side not in ("BUY","SELL") or type(qty) is not int or
                        not 1<=qty<=100000 or not isinstance(source,str) or not source):
                        raise ValueError("instrument/account mismatch")
                    value=_dec(price,negative=False)
                    if value<=0:raise ValueError("invalid execution price")
                    sign=1 if side=="BUY" else -1
                    held[(acc,sym)]+=sign*qty
                    if held[(acc,sym)]<0:raise ValueError("negative stock inventory")
                    balances[acc][cur]-=value*qty*sign
                    executed[(acc,key)]=(sym,side,qty)
                except (TypeError,ValueError,OverflowError):
                    issue("INVALID_PAPER_FILL","가상 주문 가격·계좌·상품·재고·기준시각이 불일치")
                    break
            for acc,money in balances.items():
                for ccy,amount in money.items():
                    if amount<Decimal("-0.00001"):
                        issue("NEGATIVE_ACCOUNT_CASH",acc+" 가상 "+ccy+" 잔고 음수")
            counts["positions"]=sum(1 for qty in held.values() if qty)
            if "stage1_trade_metadata" in tables:
                metadata=db.execute("""SELECT m.portfolio,m.order_key
                    FROM stage1_trade_metadata m
                    LEFT JOIN virtual_orders o ON m.owner=o.owner
                    AND m.portfolio=o.portfolio AND m.order_key=o.request_id
                    WHERE m.owner=? AND o.request_id IS NULL LIMIT 10""",(owner,)).fetchall()
                if metadata:
                    issue("ORPHAN_TRADE_METADATA","체결이 없는 거래 출처 메타데이터 발견")
            if "stage4_execution_claims" in tables:
                claims=db.execute("""SELECT request_id,account,symbol,side,
                    quantity,status,notional_krw FROM stage4_execution_claims
                    WHERE owner=? ORDER BY created_utc DESC LIMIT ?""",
                    (owner,MAX_ROWS+1)).fetchall()
                counts["stage4_claims"]=len(claims)
                if len(claims)>MAX_ROWS:
                    issue("CLAIM_SCAN_LIMIT","주문 요청 검증 상한 초과")
                for key,acc,sym,side,qty,state,notional in claims[:MAX_ROWS]:
                    expected=(sym,side,qty)
                    actual=executed.get((acc,key))
                    if state=="CLAIMED":
                        issue("INTERRUPTED_EXECUTION_CLAIM",
                              "체결 결과 미대조 요청 존재: "+str(key)[:80])
                    elif state=="EXECUTED" and actual!=expected:
                        issue("CLAIM_WITHOUT_MATCHING_FILL",
                              "완료로 표시된 주문에 일치하는 가상체결이 없음")
                    elif state=="REJECTED" and actual is not None:
                        issue("REJECTED_CLAIM_HAS_FILL",
                              "거부 요청과 실제 가상체결 기록이 충돌")
                    try:
                        if _dec(notional,negative=False)<=0:
                            issue("INVALID_CLAIM_AMOUNT","주문요청 원화 금액 오류")
                    except ValueError:
                        issue("INVALID_CLAIM_AMOUNT","주문요청 원화 금액 오류")
            # These checks do not compare historical NAV to TODAY's ledger;
            # account composition legitimately changes after old snapshots.
            if "stage5_nav_snapshots" in tables:
                from .stage5_accounting import _snapshot_digest
                nav_rows=db.execute("""SELECT as_of_day,observed_utc,nav_krw,
                    sources_sha256,ledger_sha256,snapshot_sha256
                    FROM stage5_nav_snapshots WHERE owner=? ORDER BY as_of_day DESC
                    LIMIT 181""",(owner,)).fetchall()
                counts["nav_snapshots"]=len(nav_rows)
                for day,observed,nav,source_hash,ledger_hash,digest in nav_rows:
                    if digest is None or ledger_hash is None:
                        issue("UNSIGNED_LEGACY_NAV",
                              "과거 NAV 서명 미확인 (수익률 산정 보류)",blocking=False)
                        break
                    try:
                        _timestamp(observed,now)
                        _dec(nav,negative=False)
                        if len(source_hash)!=64 or len(ledger_hash)!=64 or len(digest)!=64:
                            raise ValueError("wrong SHA length")
                        if _snapshot_digest(owner,day,observed,nav,source_hash,ledger_hash)!=digest:
                            raise ValueError("NAV value/hash mismatch")
                    except (TypeError,ValueError):
                        issue("NAV_SNAPSHOT_MISMATCH","일별 가상자산 기록 SHA·원장 시각 오류")
                        break
            if "stage1_allocations" in tables:
                splits=db.execute("""SELECT event_key,us_pct,kr_pct,isa_pct
                    FROM stage1_allocations WHERE owner=? LIMIT ?""",
                    (owner,MAX_ROWS+1)).fetchall()
                counts["funding_allocations"]=len(splits)
                if len(splits)>MAX_ROWS:issue("ALLOCATION_SCAN_LIMIT","입금 배분 검증 상한 초과")
                for key,us,kr,isa in splits[:MAX_ROWS]:
                    if key not in seen_deposits or type(us)!=int or type(kr)!=int or type(isa)!=int or not (0<=us<=100 and 0<=kr<=100 and 0<=isa<=100 and us+kr+isa==100):
                        issue("INVALID_FUNDING_ALLOCATION",
                              "입금 배분 원장과 최초 가상 입금 기록 불일치")
                        break
            counts["cash_balances"]={account:{ccy:format(val,"f")
                                      for ccy,val in values.items()}
                                      for account,values in balances.items()}
    except (sqlite3.Error,OSError,ValueError) as error:
        issue("AUDIT_READ_FAILED","읽기 전용 감사 중 원장 접근/검증 실패: "+
              type(error).__name__)
    return _response(findings,counts,now)


def _response(findings,counts,now):
    blocking=sum(1 for x in findings if x["blocking"])
    warnings=len(findings)-blocking
    return {"status":"blocked" if blocking else "warning" if warnings else "healthy",
            "engine_version":RELEASE_AUDIT,"read_only":True,
            "repair_performed":False,"restore_performed":False,
            "checked_utc":now.isoformat(),"blocking_count":blocking,
            "warning_count":warnings,"issues":findings,
            "counts":counts,
            "paper_execution_permitted_by_integrity":blocking==0,
            "note":"원장 감사를 통과해도 시세·환율·시장·긴급중지·주문 안전검사를 별도로 통과해야 합니다."}


def verify_backup_copy(portfolios,filename,owner,*,directory=None):
    """Non-destructive restoration preflight of an existing paper-only backup.

    A current computed digest is NOT evidence of a previously trusted SHA
    manifest. Never copy it into the active ledger, restore a WAL, or infer
    web-login/market-cache restoration.
    """
    if not isinstance(filename,str) or not re.fullmatch(
        r"stage1-paper-\d{8}T\d{12}Z\.sqlite3",filename):
        raise ValueError("unrecognized backup filename")
    backup_dir=Path(directory or portfolios.path.parent.parent/"backups")
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise ValueError("unsafe or missing backup directory")
    if os.name=="posix":
        st=backup_dir.stat(follow_symlinks=False)
        if st.st_uid!=os.getuid() or st.st_mode & 0o077:
            raise ValueError("insecure backup directory")
    target=_path_checked(backup_dir/filename)
    if target.stat().st_size>1024*1024*1024:
        raise ValueError("backup exceeds review size limit")
    checksum=hashlib.sha256()
    with target.open("rb") as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b""):
            checksum.update(chunk)
    integrity_ok=False;staged=False;counts={};restore_issue=None
    try:
        with closing(_readonly(target)) as db:
            result=db.execute("PRAGMA integrity_check").fetchone()
            schema={x[0] for x in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required={"virtual_orders","portfolio_transfers","stage1_events","portfolios"}
            integrity_ok=bool(result and result[0]=="ok" and required<=schema)
            if integrity_ok:
                for table in sorted(required):
                    counts[table]=db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE owner=?",(owner,)).fetchone()[0]
                if target.stat().st_size>200*1024*1024:
                    restore_issue="TEMPORARY_DRILL_TOO_LARGE"
                else:
                    # Restore into an isolated throwaway directory, never the
                    # live source, its WAL/SHM or a user-selectable path.
                    with tempfile.TemporaryDirectory(prefix=".stage7-restore-",
                                                      dir=backup_dir) as tmpdir:
                        fd,tmp=tempfile.mkstemp(prefix="stage7-",suffix=".sqlite3",
                                                dir=tmpdir)
                        os.close(fd)
                        with closing(sqlite3.connect(tmp,timeout=10)) as clone:
                            db.backup(clone)
                            verified=clone.execute("PRAGMA integrity_check").fetchone()
                            if not verified or verified[0]!="ok":
                                raise ValueError("temporary restore integrity check failed")
                            for table in sorted(required):
                                if clone.execute(
                                    f"SELECT COUNT(*) FROM {table} WHERE owner=?",
                                    (owner,)).fetchone()[0]!=counts[table]:
                                    raise ValueError("temporary restore row count mismatch")
                            staged=True
    except (sqlite3.Error,OSError,ValueError) as error:
        if not integrity_ok:
            integrity_ok=False
        restore_issue=type(error).__name__
    state=("unusable" if not integrity_ok else
           "structurally_verified" if staged else "staging_unavailable")
    return {"status":state,
            "filename":filename,"sha256_computed_now":checksum.hexdigest(),
            "trusted_manifest_hash_verified":False,
            "sqlite_integrity_check_ok":integrity_ok,
            "temporary_restore_drill_ok":staged,
            "temporary_restore_issue":restore_issue,
            "owner_rows":counts,
            "restore_performed":False,"source_overwritten":False,
            "includes":["가상거래·가상입금·계좌·승인 등이 포함된 paper SQLite"],
            "not_included":["로그인/관리자 DB","시장자료 캐시","외부 키·자격증명"],
            "notice":"임시 격리 공간에서 SQLite 사본 복원/무결성/행수를 시험한 뒤 자동 삭제합니다. 실제 운영 원장은 절대 덮어쓰지 않습니다. 신뢰할 수 있는 기존 SHA 기준값과의 일치는 별도 확인이 필요합니다."}
