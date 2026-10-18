"""Keep infrequently-used manual changes under Settings, not asset summary/search."""
from pathlib import Path

def test_manual_assets_editor_is_settings_only():
    src=(Path(__file__).resolve().parents[1]/'web/frontend/src')
    pages=(src/'pages.tsx').read_text(encoding='utf-8')
    dashboard=(src/'Stage1Dashboard.tsx').read_text(encoding='utf-8')
    app=(src/'App.tsx').read_text(encoding='utf-8')
    settings=pages.split('export function SettingsPage(',1)[1].split('export function ',1)[0]
    assets=pages.split('export function AssetsPage(',1)[1].split('export function ',1)[0]
    assert '<ManualAssetsPanel />' in settings
    assert '<ManualAssetsPanel />' not in assets
    assert 'manual-assets/balance' not in dashboard
    assert '직접 보유종목 등록' not in dashboard
    assert 'label: "설정"' in app
