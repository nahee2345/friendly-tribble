from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from wealthy_server.stage1_discovery import PreferenceStore,shortlist
from wealthy_server.stage1_research import _translate_headline


def test_preference_owner_scope_and_restart():
    with TemporaryDirectory() as root:
        path=Path(root)/'paper.sqlite3'
        store=PreferenceStore(path)
        assert store.get('one')['us_weight']==90
        assert store.save('one',95,85)['kr_weight']==5
        assert PreferenceStore(path).get('one')['etf_weight']==85
        assert store.get('two')['etf_weight']==80


def test_invalid_preferences_rejected():
    with TemporaryDirectory() as root:
        store=PreferenceStore(Path(root)/'paper.sqlite3')
        for val in [-1,101,True,'90']:
            try: store.save('owner',val,80)
            except ValueError: pass
            else: raise AssertionError('invalid allocation accepted')


def test_candidates_do_not_fake_tradeable_prices():
    with patch('wealthy_server.stage1_quotes.verified_quote',return_value={'status':'unavailable','tradeable':False}):
        result=shortlist({'us_weight':90,'etf_weight':80})
    assert result['items']
    assert all(not i['quote']['tradeable'] for i in result['items'])
    assert all(i['issuer_url'].startswith('https://') for i in result['items'])


def test_failed_translation_not_mislabeled():
    with patch('wealthy_server.stage1_research.urllib.request.urlopen',side_effect=OSError('offline')):
        _translate_headline.cache_clear()
        assert _translate_headline('Market moves higher') is None
