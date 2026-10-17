"""Paper-only SQLite backup validation with stable source and restricted destination."""
import hashlib
import sqlite3
from datetime import date

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_backup import online_paper_backup, list_paper_backups


def test_online_backup_preserves_ledger_and_is_read_only(tmp_path):
    state=tmp_path/"state"
    state.mkdir()
    location=state/"stage1-paper.sqlite3"
    paper=Stage1Ledger(location)
    portfolios=Portfolios(location)
    initialize_paper_accounts("owner",paper,portfolios,today=date(2026,10,17))
    backups=tmp_path/"backups"
    result=online_paper_backup(portfolios,directory=backups)
    assert result["status"]=="verified"
    assert result["restore_performed"] is False
    file=backups/result["filename"]
    assert file.is_file()
    assert hashlib.sha256(file.read_bytes()).hexdigest()==result["sha256"]
    assert (file.stat().st_mode & 0o077)==0
    with sqlite3.connect(file) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM stage1_events").fetchone()[0]>=1
    assert len(list_paper_backups(portfolios,directory=backups)["items"])==1
    assert len(portfolios.list("owner")["accounts"])==3
