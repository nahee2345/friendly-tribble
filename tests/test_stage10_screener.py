"""Stage10 screener contract tests, independent of live data or actual account."""
import pytest
import wealthy_server.stage10_screener as module

class Engine:
    def __init__(self,store):self.store=store
    def batch(self,items,weights,*,holdings):
        return {"ready":0,"engine_version":"fixture",
                "items":[{"symbol":x["symbol"],"status":"insufficient_data","score":None}
                         for x in items]}
def test_screen_is_read_only_and_does_not_fabricate(monkeypatch):
    monkeypatch.setattr(module,"ensure_seed",lambda p:None)
    monkeypatch.setattr(module,"search",lambda *a,**k:{
        "total":300,"is_complete":False,"catalog_status":{},
        "items":[{"symbol":"AAPL","name":"Apple","kind":"STOCK","market":"US"},
                 {"symbol":"XYZ","name":"Other","kind":"OTHER","market":"US"}]})
    monkeypatch.setattr(module,"QuantEngine",Engine)
    result=module.screen(object(),object(),{"momentum":100},limit=20)
    assert result["catalog_total"]==300 and result["screened"]==1
    assert not result["no_orders"] is False
    assert result["items"][0]["score"] is None and result["ready"]==0
def test_empty_catalog_remains_empty(monkeypatch):
    monkeypatch.setattr(module,"ensure_seed",lambda p:None)
    monkeypatch.setattr(module,"search",lambda *a,**k:{
        "total":0,"is_complete":False,"catalog_status":{},"items":[]})
    result=module.screen(object(),object(),{},limit=10)
    assert result["status"]=="no_candidates" and result["items"]==[]
@pytest.mark.parametrize("limit",[0,21,-1,True,"10"])
def test_bounded_research_limit(limit):
    with pytest.raises(ValueError):
        module.screen(None,None,{},limit=limit)
