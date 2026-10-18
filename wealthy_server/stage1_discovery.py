"""Owner-scoped market preference storage and transparent candidate research.

A candidate is not an instruction to trade. Quotes are delayed and unverified
quotes never flow into orders. No brokerage connection.
"""
from __future__ import annotations
import os
import sqlite3
import stat
from pathlib import Path
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

CANDIDATES = (
    ('VOO','Vanguard S&P 500 ETF','ETF','미국 대형주 500개 기업에 분산', 'https://investor.vanguard.com/investment-products/etfs/profile/voo'),
    ('SPYM','State Street SPDR Portfolio S&P 500 ETF','ETF','미국 대형주 지수 추종, 단일 주당 가격이 상대적으로 낮아 소액 포트폴리오에 적합할 수 있으나 가격·환율 검증 필수','https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-portfolio-sp-500-etf-spym'),
    ('VTI','Vanguard Total Stock Market ETF','ETF','미국 주식시장 전반에 분산', 'https://investor.vanguard.com/investment-products/etfs/profile/vti'),
    ('QQQM','Invesco NASDAQ 100 ETF','ETF','나스닥 100 대형 비금융주, 기술주 집중 위험', 'https://www.invesco.com/us/financial-products/etfs/product-detail?productId=QQQM'),
    ('SCHD','Schwab U.S. Dividend Equity ETF','ETF','미국 배당주 중심, 배당 변동 위험', 'https://www.schwabassetmanagement.com/products/schd'),
    ('VXUS','Vanguard Total International Stock ETF','ETF','미국 외 지역 분산, 환율 위험', 'https://investor.vanguard.com/investment-products/etfs/profile/vxus'),
    ('AAPL','Apple','STOCK','미국 개별 대형주, 집중 위험', 'https://investor.apple.com/'),
    ('MSFT','Microsoft','STOCK','미국 개별 대형주, 집중 위험', 'https://www.microsoft.com/en-us/investor'),
)

class PreferenceStore:
    def __init__(self, path:Path):
        self.path=Path(path)
        if self.path.is_symlink() or not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise ValueError('unsafe path')

    def _db(self):
        if self.path.is_symlink():raise ValueError('unsafe database')
        if self.path.exists():
            s=self.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode) or (os.name=='posix' and (s.st_uid!=os.getuid() or s.st_mode & 0o077)):
                raise ValueError('unsafe database permissions')
        c=sqlite3.connect(self.path,timeout=8)
        if os.name=='posix':os.chmod(self.path,0o600)
        c.execute('PRAGMA busy_timeout=8000')
        c.execute('CREATE TABLE IF NOT EXISTS stage1_preferences (owner TEXT PRIMARY KEY, us_weight INTEGER NOT NULL CHECK(us_weight BETWEEN 0 AND 100), etf_weight INTEGER NOT NULL CHECK(etf_weight BETWEEN 0 AND 100))')
        return c

    def get(self,owner:str)->dict:
        with self._db() as c:
            row=c.execute('SELECT us_weight,etf_weight FROM stage1_preferences WHERE owner=?',(owner,)).fetchone()
        return {'us_weight':row[0] if row else 90,'kr_weight':100-row[0] if row else 10,'etf_weight':row[1] if row else 80,'stock_weight':100-row[1] if row else 20}

    def save(self,owner:str,us_weight:int,etf_weight:int)->dict:
        for n in (us_weight,etf_weight):
            if isinstance(n,bool) or not isinstance(n,int) or not 0<=n<=100:raise ValueError('invalid allocation')
        with self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO stage1_preferences VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET us_weight=excluded.us_weight,etf_weight=excluded.etf_weight',(owner,us_weight,etf_weight))
        return self.get(owner)


def shortlist(preference:dict)->dict:
    from .stage1_quotes import verified_quote
    def inspect(candidate):
        symbol,name,kind,reason,source=candidate
        quote=verified_quote(symbol)
        return {'symbol':symbol,'name':name,'kind':kind,'market':'US','thesis':reason,
                'risks':'원금 손실·환율 변동·과거 성과 재현 불가',
                'issuer_url':source,'quote':quote,'rating':'관심 후보 (자동 추천 점수 아님)',
                'evidence':['상품 운용사 공식 자료','서버가 조회한 지연 종가'],
                'account':'해외주식 일반계좌 (국내 ISA 직접 매수 불가)'}
    # Bounded concurrent network fetching, no unbounded symbols from client.
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(inspect,CANDIDATES))
    etf_weight=preference['etf_weight']
    rows.sort(key=lambda r:(0 if (r['kind']=='ETF')==(etf_weight>=50) else 1,0 if r['quote'].get('tradeable') else 1,r['symbol']))
    return {'items':rows,'us_weight':preference['us_weight'],'kr_weight':100-preference['us_weight'],
            'etf_weight':etf_weight,'stock_weight':100-etf_weight,'kr_status':'국내 종목 검증 시세·모의 주문 미연결',
            'execution':'사용자가 직접 확인 후 가상 주문','disclaimer':'투자 수익 예측이 아닌 조사용 후보 목록입니다. 가격 확인 불가 종목은 주문할 수 없습니다.'}
