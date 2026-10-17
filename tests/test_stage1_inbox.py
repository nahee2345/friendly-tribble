from datetime import date

import pytest

from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_controls import set_paused
from wealthy_server.stage1_inbox import inbox, mark_read


def test_owner_scoped_local_alerts_with_read_receipt(tmp_path):
    location=tmp_path/"paper.sqlite3"
    ledger=Stage1Ledger(location)
    accounts=Portfolios(location)
    initialize_paper_accounts("first",ledger,accounts,today=date(2026,10,17))
    assert inbox("first",accounts)["unread"]==0
    set_paused("first",accounts,False,"로컬 가상투자 허용")
    messages=inbox("first",accounts)
    assert messages["unread"]==1
    assert messages["items"][0]["title"]=="가상 자동주문 허용"
    assert inbox("second",accounts)["items"]==[]
    assert inbox("first",accounts)["unread"]==1
    done=mark_read("first",accounts,messages["items"][0]["event_id"])
    assert done["unread"]==0
    assert inbox("first",Portfolios(location))["unread"]==0
    with pytest.raises(ValueError):
        mark_read("second",accounts,messages["items"][0]["event_id"])
