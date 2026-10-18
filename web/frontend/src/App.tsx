import { Stage1Page } from "./Stage1Page";
import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { LoginPage, useAuth } from "./auth";
import {
  AccountsPage,
  AssetsPage,
  AutomationPage,
  DividendPage,
  MarketPage,
  NotFoundPage,
  PerformancePage,
  ProviderSettingsPage,
  ReportsPage,
  RiskPage,
  SettingsPage,
  StrategyDetailPage,
  StrategyLibraryPage,
  StrategyMixPage,
  TaxPage
} from "./pages";
import { useStrategyDraft } from "./strategyDraft";
import { NotificationCenter } from "./notifications";
import { RuntimeStatusStrip } from "./runtimeStatus";

type Theme = "light" | "dark";

const navItems = [
  { to: "/stage1", icon: "◈", label: "1단계 가상계좌" },
  { to: "/dashboard", icon: "▣", label: "통합 대시보드" },
  { to: "/assets", icon: "◫", label: "내 자산" },
  { to: "/accounts", icon: "▤", label: "계좌 · ISA" },
  { to: "/strategies", icon: "◉", label: "전략 혼합" },
  { to: "/strategy-library", icon: "◇", label: "8개 전략 상세" },
  { to: "/search", icon: "⌕", label: "종목 · ETF 검색" },
  { to: "/analysis", icon: "⌁", label: "종목 · ETF 분석" },
  { to: "/tax", icon: "％", label: "세금 비교" },
  { to: "/dividends", icon: "◌", label: "배당 현금흐름" },
  { to: "/market", icon: "⌁", label: "시장정보 목록" },
  { to: "/risk", icon: "△", label: "위험 관리" },
  { to: "/performance", icon: "✓", label: "성과 검증" },
  { to: "/reports", icon: "▥", label: "보고서" },
  { to: "/automation", icon: "↻", label: "자동화 관리" },
  { to: "/providers/kis", icon: "⌘", label: "KIS API 설정" },
  { to: "/settings", icon: "⚙", label: "공통 설정" }
];

function initialTheme(): Theme {
  const stored = localStorage.getItem("wealthy-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function Shell({ theme, setTheme }: { theme: Theme; setTheme: (value: Theme) => void }) {
  const { user, logout } = useAuth();
  const draft = useStrategyDraft();
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => { setSidebarOpen(false); }, [location.pathname]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const query = search.trim();
    navigate(query ? `/search?q=${encodeURIComponent(query)}` : "/search");
  }

  async function signOut() {
    draft.clear();
    await logout();
    navigate("/", { replace: true });
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
    <button className={`sidebar-scrim ${sidebarOpen ? "visible" : ""}`} aria-label="메뉴 닫기" onClick={() => setSidebarOpen(false)} />
    <aside id="primary-navigation" className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="주 메뉴">
      <div className="logo"><span>W</span><div><strong>Wealthy</strong><small>나만의 투자 워크스페이스</small></div></div>
      <nav>{navItems.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => isActive ? "active" : ""}><span aria-hidden="true">{item.icon}</span>{item.label}</NavLink>)}</nav>
      <div className="sidebar-foot"><span className="status-dot" /> 가상 모의거래 모드<strong>실제 주문 없음</strong></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <button className="menu-button" onClick={() => setSidebarOpen(true)} aria-expanded={sidebarOpen} aria-controls="primary-navigation">☰<span className="visually-hidden">메뉴 열기</span></button>
        <form className="global-search" onSubmit={submitSearch} role="search"><span aria-hidden="true">⌕</span><label className="visually-hidden" htmlFor="global-search">종목, ETF, 전략, 보고서 검색</label><input id="global-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="종목, ETF, 전략, 보고서 검색" /><button className="visually-hidden">검색</button></form>
        <div className="top-actions"><span className="user-name">{user?.username}</span><NotificationCenter /><button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={`${theme === "light" ? "다크" : "라이트"} 모드로 전환`}>{theme === "light" ? "◐" : "☀"}</button><button className="text-button" onClick={signOut}>로그아웃</button></div>
      </header>
      <RuntimeStatusStrip />
      <main id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Stage1Page />} />
          <Route path="/stage1" element={<Stage1Page />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/strategies" element={<StrategyMixPage />} />
          <Route path="/strategy-library" element={<StrategyLibraryPage />} />
          <Route path="/strategies/:strategyId" element={<StrategyDetailPage />} />
          <Route path="/search" element={<Stage1Page />} />
          <Route path="/analysis" element={<Stage1Page />} />
          <Route path="/tax" element={<TaxPage />} />
          <Route path="/dividends" element={<DividendPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/automation" element={<AutomationPage />} />
          <Route path="/providers/kis" element={<ProviderSettingsPage />} />
          <Route path="/settings" element={<SettingsPage theme={theme} setTheme={setTheme} />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  </div>;
}

export function App() {
  const { user, loading } = useAuth();
  const [theme, setThemeState] = useState<Theme>(initialTheme);
  const setTheme = (next: Theme) => { setThemeState(next); localStorage.setItem("wealthy-theme", next); };

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b1220" : "#F7F8FA");
  }, [theme]);

  if (loading) return <main className="startup"><div className="brand-mark">W</div><p>안전한 세션을 확인하는 중입니다…</p></main>;
  if (!user) return <LoginPage />;
  return <Shell theme={theme} setTheme={setTheme} />;
}
