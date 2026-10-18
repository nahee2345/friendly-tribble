"""Guard that sidebar search/analysis routes use the real Stage1 catalog, not placeholders."""
from pathlib import Path
def test_navigation_and_research_connections():
    root=Path(__file__).resolve().parents[1]
    app=(root/'web/frontend/src/App.tsx').read_text(encoding='utf-8')
    dash=(root/'web/frontend/src/Stage1Dashboard.tsx').read_text(encoding='utf-8')
    for route in ('search','analysis'):
        assert f'<Route path="/{route}" element={{<Stage1Page />}} />' in app
    assert "['/analysis','/search'].includes(window.location.pathname)" in dash
    assert "/api/v1/stage1/securities?" in dash
    assert "/api/v1/stage1/screen?" in dash
    assert '미국 주식' in dash and '한국 주식' in dash
    assert '미국·한국 ETF' in dash
