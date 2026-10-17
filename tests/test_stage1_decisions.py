"""Policy, proposal and news result-order regression tests, no external requests."""
from pathlib import Path
from datetime import date

import pytest

from wealthy_server.stage1_bootstrap import initialize_paper_accounts
from wealthy_server.stage1_paper import Stage1Ledger
from wealthy_server.stage1_portfolios import Portfolios
from wealthy_server.stage1_discovery import PreferenceStore
from wealthy_server.stage1_strategy_policy import StrategyPolicy
from wealthy_server.stage1_decisions import proposal
from wealthy_server.stage1_news_impact import title_impact, portfolio_news_impacts


def setup(tmp_path):
    location = tmp_path / "paper.sqlite3"
    ledger = Stage1Ledger(location)
    portfolios = Portfolios(location)
    initialize_paper_accounts("owner", ledger, portfolios, today=date(2026,10,17))
    return portfolios, PreferenceStore(location), StrategyPolicy(portfolios)


def quote(symbol):
    return {"status":"ok","tradeable":True,"symbol":symbol,"price":"50" if symbol[0].isalpha() else "10000",
            "currency":"USD" if symbol[0].isalpha() else "KRW",
            "source":"fixture","as_of":"2026-10-16"}


def fx():
    return {"status":"ok","executable":False,"usd_krw":"1400","source":"fixture"}


def test_strategy_weights_validation_and_persistence(tmp_path):
    p, prefs, store = setup(tmp_path)
    initial = store.get("owner")["weights"]
    assert sum(initial.values()) == 100
    changed = {k: (100 if k=="momentum" else 0) for k in initial}
    assert store.save("owner", changed)["weights"]["momentum"] == 100
    assert StrategyPolicy(p).get("owner")["weights"] == changed
    with pytest.raises(ValueError):
        store.save("owner", {**changed,"index":10})
    assert store.get("owner")["weights"] == changed


def test_decision_respects_region_etf_account_and_quote(tmp_path):
    p,prefs,store=setup(tmp_path)
    output=proposal("owner",p,prefs,store,quote_provider=quote,fx_provider=fx)
    assert output["status"] == "ok"
    assert output["reserve_target_krw"] != "0"
    for item in output["recommendations"]:
        if item["region"] == "US":
            assert item["account"] == "default"
        elif item["kind"] == "ETF":
            assert item["account"] == "isa-base"
        assert item["price"] is not None
    assert output["no_live_orders"]


def test_unverified_quote_never_proposes_purchase(tmp_path):
    p,prefs,store=setup(tmp_path)
    bad=lambda s:{"status":"unavailable","tradeable":False,"symbol":s}
    out=proposal("owner",p,prefs,store,quote_provider=bad,fx_provider=fx)
    assert all(r["status"]=="보류" and r["quantity_suggestion"]==0
               for r in out["recommendations"])


def test_news_impact_does_not_claim_certainty(tmp_path):
    p,prefs,store=setup(tmp_path)
    p.trade("owner","isa-base","360750","BUY",1,"manual-isa-0001",quote("360750"))
    feeds=lambda region: {"status":"ok","items":[
        {"title_original":"S&P 500 drops sharply","url":"https://news.google.com/test",
         "title_ko":None,"published":"fixture"}] if region=="US" else []}
    response=portfolio_news_impacts("owner",p,provider=feeds)
    assert response["summary"]["headline_matches"] == 1
    assert response["impacts"][0]["symbols"] == ["360750"]
    assert "검토" in response["impacts"][0]["outcome"]
    assert response["impacts"][0]["action"].startswith("확인 필요")
