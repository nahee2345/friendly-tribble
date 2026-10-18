"""Stage9 offline CSV import tests; temporary databases only."""
import sqlite3
from contextlib import contextmanager
import pytest
from wealthy_server.stage1_catalog import search,ensure_seed
from wealthy_server.stage9_catalog_import import import_listing

class Store:
    def __init__(self,p):self.p=p
    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.p)
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:c.close()

def listing(tmp_path,text):
    p=tmp_path/"symbols.csv";p.write_text(text,encoding="utf-8");return p

def test_bulk_us_12000(tmp_path):
    s=Store(tmp_path/"paper.db")
    csv="symbol,name,kind\n"+ "".join(f"A{i:05d},Company {i},STOCK\n" for i in range(12000))
    result=import_listing(s,listing(tmp_path,csv),market="US",source="fixture")
    assert result["imported"]==12000 and not result["tradeable"]
    assert search(s,"A00001","US","STOCK")["total"]==1

def test_kr_etf(tmp_path):
    s=Store(tmp_path/"paper.db")
    import_listing(s,listing(tmp_path,"symbol,name,kind\n069500,KODEX 200,ETF\n005930,삼성전자,STOCK\n"),market="KR",source="fixture")
    assert search(s,"KODEX","KR","ETF")["total"]==1
    assert search(s,"삼성","KR","STOCK")["total"]==1

@pytest.mark.parametrize("bad",[
    "symbol,name,kind\nAAPL,Apple,STOCK\nAAPL,Duplicate,ETF\n",
    "symbol,name,kind\nAAPL,Apple,STOCK\nINVALID$,Invalid,ETF\n",
    "symbol,name,kind\nAAPL,Apple,STOCK\nMSFT,Microsoft,UNKNOWN\n",
    "symbol,name,kind\n",
    "symbol,name,kind\nAAPL,Apple,STOCK,EXTRA\n",
])
def test_invalid_rejected_without_changes(tmp_path,bad):
    s=Store(tmp_path/"paper.db");ensure_seed(s)
    count=search(s)["total"]
    with pytest.raises(ValueError):
        import_listing(s,listing(tmp_path,bad),market="US",source="fixture")
    assert search(s)["total"]==count

def test_symlink_refused(tmp_path):
    s=Store(tmp_path/"paper.db")
    f=listing(tmp_path,"symbol,name,kind\nAAPL,Apple,STOCK\n")
    link=tmp_path/"link.csv";link.symlink_to(f)
    with pytest.raises(ValueError):
        import_listing(s,link,market="US",source="fixture")
