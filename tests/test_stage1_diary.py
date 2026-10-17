from datetime import date
from wealthy_server.stage1_diary import save_daily_paper_report, list_daily_paper_reports
from wealthy_server.stage1_portfolios import Portfolios


def test_daily_reports_are_deduplicated_and_owner_scoped(tmp_path):
    accounts=Portfolios(tmp_path/'paper.sqlite3')
    us={'status':'skipped','traded':False,'reason':'시세 없음'}
    kr={'results':[{'account':'kr-base','status':'skipped','reason':'가격 없음'}]}
    exits={'events':[]}
    save_daily_paper_report('a',accounts,us=us,domestic=kr,exits=exits)
    save_daily_paper_report('a',accounts,us=us,domestic=kr,exits=exits)
    result=list_daily_paper_reports('a',accounts)
    assert len(result['items'])==1
    assert result['items'][0]['us']['status']=='skipped'
    assert list_daily_paper_reports('other',accounts)['items']==[]
    assert result['simulated_only'] is True
