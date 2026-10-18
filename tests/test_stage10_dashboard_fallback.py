"""Guard against loss of visible owner cash when external panels fail."""
from wealthy_server.stage1_hub import dashboard

class Portfolio:
    def list(self,owner):
        assert owner=="owner1"
        return {"accounts":[{"id":"default","name":"미국 일반","krw_cash":"3200000",
                             "usd_cash":"0","holdings":[]},
                            {"id":"kr-base","name":"한국 일반","krw_cash":"400000",
                             "usd_cash":"0","holdings":[]}]}

def explode():
    raise OSError("feed disconnected")

def test_owner_cash_visible_when_external_panels_fail(monkeypatch):
    import wealthy_server.stage1_hub as h
    monkeypatch.setattr(h,"asset_history",lambda *a,**k:explode())
    result=dashboard("owner1",Portfolio(),object(),object(),market_provider=explode,
       report_provider=lambda:{"complete":False,"portfolio_value_krw":None,
         "contributions_krw":4000000,"return_krw":None},
       decision_provider=explode)
    assert result["accounts"][0]["krw_cash"]=="3200000"
    assert result["accounts"][1]["krw_cash"]=="400000"
    assert all(m["status"]=="unavailable" for m in result["markets"])
    assert result["recommendations"]==[]
    assert set(result["partial_errors"])=={"valuation_unavailable","decision_unavailable","markets_unavailable"}
    assert result["performance"]["complete"] is False
