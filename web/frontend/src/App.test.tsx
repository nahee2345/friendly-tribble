import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { StrategyDraftProvider } from "./strategyDraft";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

function renderApp(path = "/dashboard") {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><StrategyDraftProvider><App /></StrategyDraftProvider></AuthProvider></MemoryRouter>);
}

describe("Wealthy 웹 셸", () => {
  it("인증 후 16개 실제 화면 메뉴와 주문 금지 상태를 표시한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/dashboard")) return json({ data_scope: null, investable_total_krw: null, warnings: [] });
      return json({}, 404);
    });
    renderApp();
    await screen.findByRole("heading", { name: "통합 대시보드" });
    expect(screen.getAllByRole("navigation")[0].querySelectorAll("a")).toHaveLength(16);
    expect(screen.getByRole("link", { name: "시장정보 목록" })).toHaveAttribute("href", "/market");
    expect(screen.getByText("주문 기능 없음")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("미입력").length).toBeGreaterThan(0));
  });

  it("전략 목표 배분과 후보 평가 가중치가 다름을 고정 안내한다", async () => {
    const weights = {
      LONG_TERM_INDEX: "25", VALUE: "15", QUALITY_GROWTH: "15", GROWTH: "10",
      HIGH_DIVIDEND: "15", DIVIDEND_GROWTH: "10", MOMENTUM: "5", ASSET_ALLOCATION: "5"
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/strategy-targets/current")) return json({ target: { version: "v1", weights } });
      if (url.endsWith("/strategy-catalog")) return json({ items: [] });
      if (url.endsWith("/strategy-targets/preview")) return json({ valid: true, total: "100", target_amounts_krw: {} });
      if (url.endsWith("/dashboard")) return json({ data_scope: "MISSING", investable_total_krw: null });
      return json({}, 404);
    });
    renderApp("/strategies");
    await screen.findByRole("heading", { name: "투자전략 혼합 설정" });
    expect(screen.getByText(/후보 평가용 점수 가중치와 다르며/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "새 불변 버전 저장" })).toBeDisabled());
  });

  it("로컬 동일 출처의 전역 실행·자료·주문 비활성 상태를 표시한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/status")) return json({ mode: "LOCAL_WEB_READ_ONLY_ANALYSIS", database: "CONNECTED_LOCAL_SQLITE", scheduler: "ACTIVE", orders_enabled: false, providers: [{ provider: "kis", status: "NOT_CONFIGURED" }] });
      if (url.endsWith("/dashboard")) return json({ data_scope: "SAMPLE", as_of: "2026-10-11T06:00:00Z", stale: true, investable_total_krw: null, warnings: [] });
      if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
      return json({}, 404);
    });
    renderApp();
    await screen.findByRole("heading", { name: "통합 대시보드" });
    const strip = await screen.findByRole("region", { name: "현재 앱과 자료 상태" });
    expect(strip).toHaveTextContent("LOCALHOST_ONLY");
    expect(strip).toHaveTextContent("SQLite");
    expect(strip).toHaveTextContent("샘플 자료");
    expect(strip).toHaveTextContent("미갱신");
    expect(strip).toHaveTextContent("주문 비활성");
  });

  it("전략 초안·저장 목표·실제 비중·불변 이력을 섞지 않는다", async () => {
    const weights = {
      LONG_TERM_INDEX: "25", VALUE: "15", QUALITY_GROWTH: "15", GROWTH: "10",
      HIGH_DIVIDEND: "15", DIVIDEND_GROWTH: "10", MOMENTUM: "5", ASSET_ALLOCATION: "5"
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/strategy-targets/current")) return json({ target: { id: "s-2", version: "saved-v2", created_at: "2026-10-11T07:00:00Z", weights } });
      if (url.endsWith("/strategy-targets/history")) return json({ items: [{ id: "s-1", version: "saved-v1", created_at: "2026-10-10T07:00:00Z", weights }] });
      if (url.endsWith("/strategy-catalog")) return json({ items: [] });
      if (url.endsWith("/strategy-targets/preview")) return json({ valid: true, total: "100", target_amounts_krw: {} });
      if (url.endsWith("/portfolio")) return json({ accounts: [], positions: [], asset_class_totals: [], monthly_history: [], actual_strategy: { status: "MISSING", weights: null, missing_reason: "귀속 자료 미입력" } });
      if (url.endsWith("/dashboard")) return json({ data_scope: "MISSING", investable_total_krw: null });
      if (url.endsWith("/status")) return json({ orders_enabled: false });
      if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
      return json({}, 404);
    });
    renderApp("/strategies");
    await screen.findByRole("heading", { name: "투자전략 혼합 설정" });
    expect(screen.getByRole("region", { name: "전략 초안, 저장 목표와 실제 비중 구분" })).toHaveTextContent("편집 초안");
    expect(screen.getByRole("region", { name: "전략 초안, 저장 목표와 실제 비중 구분" })).toHaveTextContent("저장 목표");
    expect(screen.getByRole("region", { name: "전략 초안, 저장 목표와 실제 비중 구분" })).toHaveTextContent("실제 비중");
    expect(await screen.findByText("saved-v1")).toBeInTheDocument();
    expect(screen.getByText("귀속 자료 미입력")).toBeInTheDocument();
    expect(screen.getByText("설정 필요")).toBeInTheDocument();
  });

  it("포트폴리오 결측을 0원·가상 종목으로 바꾸지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/portfolio")) return json({ data_scope: "MISSING", investable_total_krw: null, accounts: [], positions: [], asset_class_totals: [], monthly_history: [], actual_strategy: null });
      if (url.endsWith("/dashboard")) return json({ data_scope: "MISSING", investable_total_krw: null });
      if (url.endsWith("/status")) return json({ orders_enabled: false });
      if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
      return json({}, 404);
    });
    renderApp("/assets");
    await screen.findByRole("heading", { name: "내 자산 · 월별 정리" });
    await screen.findByText(/자산군 합계가 없습니다/);
    expect(screen.getAllByText("미입력").length).toBeGreaterThan(0);
    expect(screen.getByText(/샘플 종목을 실제 보유로 표시하지 않습니다/)).toBeInTheDocument();
    expect(screen.queryByText("₩0")).not.toBeInTheDocument();
  });

  it("보고서 이력에서 저장 당시 자료 영역과 생성 종류를 구분하고 과거 결측을 추정하지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/reports")) return json({ items: [
        { id: 3, created_at: "2026-10-11T09:00:00Z", status: "OK", data_scope: "MANUAL_ACTUAL", generation_kind: "MANUAL" },
        { id: 2, created_at: "2026-10-11T08:00:00Z", status: "OK", data_scope: "SAMPLE", generation_kind: "MANUAL" },
        { id: 1, created_at: "2026-10-11T07:00:00Z", status: "OK", data_scope: null, generation_kind: null }
      ] });
      if (url.endsWith("/status")) return json({ orders_enabled: false });
      if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
      return json({}, 404);
    });
    renderApp("/reports");
    const history = (await screen.findByRole("heading", { name: "보고서 이력" })).closest("section");
    expect(history).not.toBeNull();
    const reportList = within(history as HTMLElement);
    expect(await reportList.findAllByText("수동 생성")).toHaveLength(2);
    expect(reportList.getByText("MANUAL_ACTUAL · 수동 실제 자료", { exact: false })).toBeInTheDocument();
    expect(reportList.getByText("SAMPLE · 샘플 자료", { exact: false })).toBeInTheDocument();
    expect(reportList.getAllByText("확인 불가").length).toBeGreaterThanOrEqual(1);
    expect(reportList.queryByText("정기 보고서")).not.toBeInTheDocument();
    expect(reportList.queryByText("자료 미입력")).not.toBeInTheDocument();
  });

  it("로그인한 페이지에서만 로컬 알림함을 열고 PAGE_OPEN_ONLY 경계를 표시한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/notifications")) return json({
        browser_notifications: "PAGE_OPEN_ONLY",
        items: [{ id: "notice-1", kind: "REPORT", title: "보고서 생성 상태", message: "상세 내용은 로컬 알림함에서만 확인합니다.", created_at: "2026-10-11T07:00:00Z", status: "UNREAD" }]
      });
      if (url.endsWith("/dashboard")) return json({ data_scope: "MISSING", investable_total_krw: null });
      if (url.endsWith("/status")) return json({ mode: "LOCALHOST_ONLY", database: "SQLite", orders_enabled: false });
      return json({}, 404);
    });
    renderApp();
    const button = await screen.findByRole("button", { name: "알림함 열기" });
    button.click();
    expect(await screen.findByText("보고서 생성 상태")).toBeInTheDocument();
    expect(screen.getByText(/PAGE_OPEN_ONLY/)).toBeInTheDocument();
    expect(screen.getByText(/페이지가 열려 있을 때만/)).toBeInTheDocument();
  });

  it("백엔드가 확인한 경우에만 보안 저장소 사용 불가를 표시하고 재검사 호출을 만들지 않는다", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/credentials")) return json({
        items: [],
        storage: { available: false, confirmed: true, backend: "UNAVAILABLE", plaintext_fallback: false, safe_recheck_allowed: false, checked_at: "2026-10-14T03:00:00Z" },
      });
      if (url.endsWith("/status")) return json({ orders_enabled: false });
      if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
      return json({}, 404);
    });
    renderApp("/providers/kis");
    expect(await screen.findByText("보안 저장소 사용 불가", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/원인은 단정하지 않으며/)).toBeInTheDocument();
    expect(screen.queryByText(/잠금/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "상태 다시 확인" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "암호화 저장" })).toBeDisabled();
    expect(calls.some((url) => url.includes("storage/recheck"))).toBe(false);
  });

  it("확인되지 않은 보안 저장소 상태를 사용 불가로 추정하지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
      if (url.endsWith("/credentials")) return json({ items: [], storage: { available: null, confirmed: false, backend: "UNKNOWN", plaintext_fallback: false, safe_recheck_allowed: false } });
      return json({}, 404);
    });
    renderApp("/providers/kis");
    await screen.findByRole("heading", { name: "KIS API 설정" });
    await waitFor(() => expect(screen.getByRole("button", { name: "암호화 저장" })).toBeEnabled());
    expect(screen.queryByText("보안 저장소 사용 불가")).not.toBeInTheDocument();
  });
});
