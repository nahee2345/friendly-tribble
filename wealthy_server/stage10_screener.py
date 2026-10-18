"""Read-only, capped catalog screening based on already verified market cache."""
from .stage1_catalog import ensure_seed, search
from .stage1_quantitative import QuantEngine

def screen(portfolios, market_store, weights, *, q="",market="ALL",kind="ALL",
           limit=20,offset=0,holdings=()):
    if type(limit) is not int or not 1<=limit<=20:
        raise ValueError("screen limit must be 1..20")
    ensure_seed(portfolios)
    listing=search(portfolios,q,market,kind,limit,offset)
    items=[{"symbol":x["symbol"],"name":x["name"],"kind":x["kind"],
            "region":x["market"]} for x in listing["items"] if x["kind"] in ("STOCK","ETF")]
    base={"catalog_total":listing["total"],"catalog_complete":listing["is_complete"],
          "catalog_status":listing["catalog_status"],"page_offset":offset,
          "screened":len(items),"read_only":True,"no_orders":True,
          "notice":"종목 검색과 매매 허용은 별개입니다. 정량 점수는 수익 예측이나 자동 주문 신호가 아닙니다."}
    if not items:return {**base,"items":[],"ready":0,"status":"no_candidates"}
    assessments=QuantEngine(market_store).batch(items,weights,holdings=holdings)
    return {**base,"status":"ok","ready":assessments["ready"],
            "items":assessments["items"],"engine_version":assessments["engine_version"]}
