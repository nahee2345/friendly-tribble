import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { safeExternalHref } from "./marketLinks";
import { StrategyDraftProvider } from "./strategyDraft";
import type { MarketInformationItem } from "./types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

function item(overrides: Partial<MarketInformationItem> = {}): MarketInformationItem {
  return {
    info_id: "fed-1",
    provider_id: "fed",
    provider_name: "Federal Reserve",
    source_name: "Federal Reserve Press Release",
    title_original: "Minutes of the Federal Open Market Committee, September 15-16, 2026",
    language: "en",
    category: "ECONOMY",
    related_symbols: [],
    holding_status: "UNKNOWN",
    original_url: "https://www.federalreserve.gov/newsevents/pressreleases/example.htm",
    link_status: "SAFE",
    published_at: "2026-10-12",
    collected_at: "2026-10-13T06:00:00Z",
    provider_item_id: "fed-item-1",
    source_document_id: "fed-doc-1",
    published_precision: "DATE",
    is_sample: false,
    sources: [],
    ...overrides
  };
}

function commonResponse(url: string) {
  if (url.endsWith("/session")) return json({ authenticated: true, user: { id: "u-1", username: "admin" } });
  if (url === "/api/v1/status") return json({ mode: "LOCALHOST_ONLY", database: "SQLite", orders_enabled: false });
  if (url.endsWith("/dashboard")) return json({ data_scope: "MISSING", investable_total_krw: null });
  if (url.endsWith("/notifications")) return json({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
  return null;
}

function renderMarket() {
  return render(<MemoryRouter initialEntries={["/market"]}><AuthProvider><StrategyDraftProvider><App /></StrategyDraftProvider></AuthProvider></MemoryRouter>);
}

describe("미국 시장정보 화면", () => {
  it("원문·날짜 정밀도·보유 상태를 표시하고 외부 제목과 위험 링크를 실행하지 않는다", async () => {
    const safe = item({ related_symbols: ["US0378331005"], holding_status: "HELD" });
    const unsafe = item({
      info_id: "sec-unsafe",
      provider_id: "sec",
      provider_name: "SEC",
      source_name: "SEC News",
      title_original: "<img src=x onerror=alert(1)> **not markup**",
      original_url: "http://127.0.0.1/private",
      link_status: "SAFE",
      published_at: null,
      published_precision: "UNKNOWN"
    });
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "ENABLED_WHILE_BACKEND_RUNNING", last_success_at: "2026-10-13T06:00:00Z", refreshing: false, partial: true, providers: [{ provider_id: "fed", provider_name: "Federal Reserve", source_name: "Federal Reserve Press Release", status: "OK", last_success_at: "2026-10-13T06:00:00Z", next_retry_at: "2026-10-13T07:00:00Z" }, { provider_id: "bea", provider_name: "BEA", source_name: "BEA Releases", status: "NOT_MODIFIED", last_success_at: "2026-10-13T06:00:00Z", next_retry_at: "2026-10-13T07:00:00Z" }, { provider_id: "sec", provider_name: "SEC", source_name: "SEC Press Releases", status: "FAILED", last_success_at: null, next_retry_at: "2026-10-13T08:00:00Z", stale: true }] });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [safe, unsafe], next_cursor: null, snapshot: "snapshot-1", has_more: false });
      return json({}, 404);
    });

    const view = renderMarket();
    expect(await screen.findByRole("heading", { name: "미국 시장정보" })).toBeInTheDocument();
    expect(await screen.findByText(safe.title_original)).toBeInTheDocument();
    expect(screen.getByText("2026. 10. 12. (날짜 정밀도)")).toBeInTheDocument();
    expect(screen.getByText("발행시각 미확인")).toBeInTheDocument();
    expect(screen.getByText(unsafe.title_original)).toBeInTheDocument();
    expect(view.container.querySelector("img")).toBeNull();
    expect(screen.getByText("US0378331005")).toHaveClass("held-symbol");
    expect(screen.getByText("보유")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "원문 보기 (외부 사이트, 새 탭)" });
    expect(link).toHaveAttribute("href", safe.original_url ?? "");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("원문 링크 사용 불가")).toBeInTheDocument();
    expect(screen.getByText(/일부 공급자는 설정이 필요하거나 지연/)).toBeInTheDocument();
    const providerStatuses = screen.getByText("공급자별 상태 3개").closest("details");
    expect(providerStatuses).not.toBeNull();
    await userEvent.setup().click(screen.getByText("공급자별 상태 3개"));
    expect(within(providerStatuses as HTMLElement).getByText("Federal Reserve Press Release")).toBeInTheDocument();
    expect(within(providerStatuses as HTMLElement).getByText("BEA Releases")).toBeInTheDocument();
    expect(within(providerStatuses as HTMLElement).getByText("SEC Press Releases")).toBeInTheDocument();
    expect(within(providerStatuses as HTMLElement).getByText(/이번 수집 전체 정상/)).toBeInTheDocument();
    expect(within(providerStatuses as HTMLElement).getAllByText(/다음 확인/)).toHaveLength(2);
    expect(within(providerStatuses as HTMLElement).getByText(/재시도/)).toBeInTheDocument();
    expect(providerStatuses?.textContent).not.toContain("$");
    expect(screen.getByText("활성(백엔드 실행 중 자동)")).toBeInTheDocument();
  });

  it.each([
    ["RUNNING", false, "실행 중"],
    ["ENABLED_WHILE_BACKEND_RUNNING", false, "활성(백엔드 실행 중 자동)"],
    ["STOPPED", false, "중지"],
    ["ENABLED_WHILE_BACKEND_RUNNING", true, "실행 중"],
    ["NOT_RUNNING", false, "확인 필요"],
  ])("자동 상태 %s / refreshing=%s를 정확히 표시한다", async (automaticState, refreshing, expected) => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: automaticState, last_success_at: null, refreshing, partial: false, providers: [] });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [], next_cursor: null, snapshot: "empty", has_more: false });
      return json({}, 404);
    });

    renderMarket();
    await screen.findByRole("heading", { name: "미국 시장정보" });
    const automaticCard = screen.getByText("자동 갱신").closest("article");
    expect(automaticCard).not.toBeNull();
    await waitFor(() => {
      expect(within(automaticCard as HTMLElement).getByText(expected)).toBeInTheDocument();
    });
  });

  it("새 정보가 와도 읽던 목록과 필터를 유지하고 사용자가 반영할 때만 바꾼다", async () => {
    const user = userEvent.setup();
    const oldItem = item({ info_id: "old", title_original: "Existing official item" });
    const newItem = item({ info_id: "new", title_original: "New official item", collected_at: "2026-10-13T06:05:00Z" });
    let latestAvailable = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "ACTIVE", last_success_at: "2026-10-13T06:00:00Z", refreshing: false, partial: false, providers: [{ provider_id: "fed", provider_name: "Federal Reserve", status: "SUCCESS" }] });
      if (url.startsWith("/api/v1/market-information?")) {
        const parsed = new URL(url, "http://localhost");
        const values = latestAvailable ? [newItem, oldItem] : [oldItem];
        return json({ items: values, next_cursor: null, snapshot: latestAvailable ? "snapshot-2" : "snapshot-1", has_more: false, probe_limit: parsed.searchParams.get("limit") });
      }
      return json({}, 404);
    });

    renderMarket();
    await screen.findByText("Existing official item");
    await user.click(screen.getByRole("button", { name: "경제" }));
    await screen.findByText("Existing official item");
    expect(screen.getByRole("button", { name: "경제" })).toHaveAttribute("aria-pressed", "true");

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.documentElement.scrollTop = 240;
    latestAvailable = true;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(await screen.findByText("새 정보 1건")).toBeInTheDocument();
    expect(screen.queryByText("New official item")).not.toBeInTheDocument();
    expect(screen.getByText("Existing official item")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "경제" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.scrollTop).toBe(240);

    await user.click(screen.getByRole("button", { name: "새 정보 보기" }));
    expect(await screen.findByText("New official item")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("새 정보 1건")).not.toBeInTheDocument());
    const rows = screen.getByRole("heading", { name: "시장정보 목록" }).closest("section");
    expect(rows).not.toBeNull();
    expect(within(rows as HTMLElement).getAllByRole("article")[0]).toHaveAttribute("data-info-id", "new");
    expect(screen.getByRole("button", { name: "경제" })).toHaveAttribute("aria-pressed", "true");
  });

  it("공급자가 모두 미설정이면 자료 없음과 구분한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "STOPPED", last_success_at: null, refreshing: false, partial: false, providers: [{ provider_id: "sec", provider_name: "SEC", status: "NOT_CONFIGURED" }] });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [], next_cursor: null, snapshot: "empty", has_more: false });
      return json({}, 404);
    });
    renderMarket();
    expect(await screen.findByRole("heading", { name: "공급자 미설정" })).toBeInTheDocument();
    expect(screen.getByText("중지")).toBeInTheDocument();
  });

  it("SEC 연락정보는 기존 보안 저장 API로 보내고 원문을 다시 표시하지 않는다", async () => {
    const user = userEvent.setup();
    const contact = "Fixture Team contact@example.invalid";
    let savedBody = "";
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "STOPPED", last_success_at: null, refreshing: false, partial: false, providers: [{ provider_id: "sec_press", institution_id: "sec", provider_name: "SEC", source_name: "SEC Press", status: "NEEDS_CONFIGURATION" }] });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [], next_cursor: null, snapshot: "empty", has_more: false });
      if (url === "/api/v1/credentials/sec/default" && init?.method === "PUT") {
        savedBody = String(init.body ?? "");
        return json({ provider: "sec", environment: "default", configured: true });
      }
      return json({}, 404);
    });

    renderMarket();
    await screen.findByRole("heading", { name: "미국 시장정보" });
    await user.click(screen.getByText("SEC 자동조회 설정"));
    const input = screen.getByLabelText("SEC User-Agent 연락정보");
    await user.type(input, contact);
    await user.click(screen.getByRole("button", { name: "등록 · 교체" }));

    expect(await screen.findByText(/OS 보안 저장소에 등록했습니다/)).toBeInTheDocument();
    expect(savedBody).toContain("user_agent");
    expect(savedBody).toContain(contact);
    expect(input).toHaveValue("");
    expect(screen.queryByText(contact)).not.toBeInTheDocument();
  });

  it("시장정보 설정도 백엔드가 확인한 보안 저장소 사용 불가만 표시한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/credentials") return json({ items: [], storage: { available: false, confirmed: true, backend: "UNAVAILABLE", plaintext_fallback: false, safe_recheck_allowed: false } });
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "STOPPED", last_success_at: null, refreshing: false, partial: false, providers: [{ provider_id: "sec_press", provider_name: "SEC", status: "NEEDS_CONFIGURATION" }] });
      if (url.startsWith("/api/v1/market-information/retention?")) return json({ summary: { total_information_count: 0, total_source_record_count: 0, expired_count: 0, expiring_soon_count: 0, uncomputable_count: 0, checked_at: null }, items: [], next_cursor: null, snapshot: "empty", has_more: false, deletion_enabled: false });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [], next_cursor: null, snapshot: "empty", has_more: false });
      return json({}, 404);
    });
    renderMarket();
    await screen.findByRole("heading", { name: "미국 시장정보" });
    await userEvent.setup().click(screen.getByText("SEC 자동조회 설정"));
    expect(screen.getByText("보안 저장소 사용 불가", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/확인되지 않은 원인을 단정하거나/)).toBeInTheDocument();
    expect(screen.queryByText(/잠금/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("SEC User-Agent 연락정보")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "상태 다시 확인" })).not.toBeInTheDocument();
  });

  it("검색·정렬·커서 페이지 이동에 현재 snapshot을 유지한다", async () => {
    const user = userEvent.setup();
    const first = item({ info_id: "page-1", title_original: "First filing" });
    const second = item({ info_id: "page-2", title_original: "Second filing" });
    const requested: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "ACTIVE", last_success_at: null, refreshing: false, partial: false, providers: [] });
      if (url.startsWith("/api/v1/market-information?")) {
        requested.push(url);
        const parsed = new URL(url, "http://localhost");
        if (parsed.searchParams.get("cursor") === "cursor-2") return json({ items: [second], next_cursor: null, snapshot: "stable-snapshot", has_more: false });
        return json({ items: [first], next_cursor: parsed.searchParams.get("limit") === "25" ? "cursor-2" : null, snapshot: "stable-snapshot", has_more: parsed.searchParams.get("limit") === "25" });
      }
      return json({}, 404);
    });

    renderMarket();
    await screen.findByText("First filing");
    await user.selectOptions(screen.getByLabelText("정렬"), "COLLECTED_DESC");
    await screen.findByText("First filing");
    await user.type(screen.getByLabelText("시장정보 검색"), "SEC filing");
    await user.click(
      within(screen.getByRole("search", { name: "시장정보 검색·필터" })).getByRole("button", {
        name: "검색",
      }),
    );
    await screen.findByText("First filing");
    await user.click(screen.getByRole("button", { name: "더 보기" }));
    expect(await screen.findByText("Second filing")).toBeInTheDocument();
    const pageRequest = requested.map((value) => new URL(value, "http://localhost")).find((value) => value.searchParams.get("cursor") === "cursor-2");
    expect(pageRequest?.searchParams.get("snapshot")).toBe("stable-snapshot");
    expect(pageRequest?.searchParams.get("q")).toBe("SEC filing");
    expect(pageRequest?.searchParams.get("sort")).toBe("COLLECTED_DESC");
  });

  it("작업 상태와 현재 자료 상태를 분리하고 부분 실패·시간 초과·이전 정상 자료를 표시한다", async () => {
    const retained = item({
      first_collected_at: "2025-10-13T06:00:00Z",
      retention_expires_at: "2026-10-13T06:00:00Z",
      retention_status: "EXPIRED",
      first_collected_estimated: true,
    });
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/credentials") return json({ items: [], storage: { available: true, confirmed: true, plaintext_fallback: false, safe_recheck_allowed: false } });
      if (url === "/api/v1/market-information/status") return json({
        automatic_state: "ENABLED_WHILE_BACKEND_RUNNING",
        refreshing: false,
        last_attempt_at: "2026-10-14T06:00:03Z",
        last_success_at: "2026-10-14T05:00:00Z",
        current_data_as_of: "2026-10-14T05:00:00Z",
        current_result: "PARTIAL",
        overall_state: "PARTIAL",
        partial: true,
        failed_provider_ids: ["sec_press"],
        timed_out_provider_ids: ["federal_reserve"],
        incomplete_provider_ids: ["bea"],
        using_previous_data: true,
        retry_allowed: false,
        next_retry_at: "2026-10-14T06:30:00Z",
        collection_timeout_seconds: 45,
        timeout_limitations: "DNS 해석 호출 자체는 진행 중 강제 취소할 수 없습니다.",
        providers: [
          { provider_id: "federal_reserve", provider_name: "Fed", status: "TIMED_OUT", this_collection_result: "TIMED_OUT", last_attempt_at: "2026-10-14T06:00:00Z", last_success_at: "2026-10-14T05:00:00Z", current_data_as_of: "2026-10-14T05:00:00Z", using_previous_data: true, retry_allowed: false, next_retry_at: "2026-10-14T06:30:00Z", error_code: "COLLECTION_TIMEOUT" },
          { provider_id: "sec_press", provider_name: "SEC", status: "FAILED", this_collection_result: "FAILED", last_attempt_at: "2026-10-14T06:00:02Z", last_success_at: null, current_data_as_of: null, success_recorded: false, retry_allowed: false, error_code: "NETWORK" },
          { provider_id: "bea", provider_name: "BEA", status: "WAITING", this_collection_result: "NOT_STARTED_TIME_LIMIT", this_collection_error_code: "COLLECTION_TIME_LIMIT_REACHED", last_attempt_at: null, last_success_at: null, current_data_as_of: null, retry_allowed: true },
        ],
      });
      if (url.startsWith("/api/v1/market-information/retention?")) return json({
        summary: { total_information_count: 8, total_source_record_count: 11, expired_count: 1, expiring_soon_count: 2, uncomputable_count: 1, checked_at: "2026-10-14T06:05:00Z" },
        items: [{ info_id: "expired-1", provider_id: "fed", source_name: "Fed release", title_original: "Expired metadata", first_collected_at: "2025-10-13T06:00:00Z", retention_expires_at: "2026-10-13T06:00:00Z", retention_status: "EXPIRED", first_collected_estimated: true }],
        next_cursor: null, snapshot: "retention-1", has_more: false, deletion_enabled: false,
      });
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [retained], next_cursor: null, snapshot: "items-1", has_more: false });
      return json({}, 404);
    });

    renderMarket();
    expect(await screen.findByText("부분 완료(일부 실패)")).toBeInTheDocument();
    expect(screen.getByText(/시간 초과 공급자: federal_reserve/)).toBeInTheDocument();
    expect(screen.getByText(/제한시간 내 미시작 공급자: bea/)).toBeInTheDocument();
    expect(screen.getByText(/실패 공급자: sec_press/)).toBeInTheDocument();
    expect(screen.getByText(/이전 정상 자료를 사용 중/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "재시도 대기" })).toBeDisabled();
    const providerPanel = screen.getByText("공급자별 상태 3개").closest("details");
    expect(providerPanel).not.toBeNull();
    await userEvent.setup().click(screen.getByText("공급자별 상태 3개"));
    expect(within(providerPanel as HTMLElement).getByText(/이번 수집 시간 초과/)).toBeInTheDocument();
    expect(within(providerPanel as HTMLElement).getByText(/이번 수집 전체 실패/)).toBeInTheDocument();
    expect(within(providerPanel as HTMLElement).getByText(/이전 정상 자료 사용/)).toBeInTheDocument();
    const noSuccessRow = providerPanel?.querySelector('[data-provider-id="sec_press"]');
    expect(noSuccessRow).not.toBeNull();
    expect(within(noSuccessRow as HTMLElement).getByText(/최근 성공 기록 없음$/)).toBeInTheDocument();
    expect(noSuccessRow?.textContent).not.toContain("최근 성공 성공 기록 없음");
    expect(within(providerPanel as HTMLElement).getByText(/COLLECTION_TIME_LIMIT_REACHED/)).toBeInTheDocument();
    expect(within(providerPanel as HTMLElement).getByText(/이번 수집 제한시간 내 미시작/)).toBeInTheDocument();
    const retentionPanel = (await screen.findByRole("heading", { name: "보존 기간 · 삭제 대상 미리보기" })).closest("section");
    expect(retentionPanel).not.toBeNull();
    expect(screen.getByText("현재는 삭제 대상 확인만 지원합니다. 실제 삭제 및 자동 정리 기능은 활성화되어 있지 않습니다.")).toBeInTheDocument();
    expect(screen.getByText("8건")).toBeInTheDocument();
    expect(screen.getByText("11건")).toBeInTheDocument();
    expect(screen.getByText("Expired metadata")).toBeInTheDocument();
    expect(screen.getAllByText((_, element) => element?.textContent?.includes("2025") === true && element.textContent.includes("(추정)")).length).toBeGreaterThanOrEqual(1);
    expect(within(retentionPanel as HTMLElement).queryByRole("button", { name: /^삭제$/ })).not.toBeInTheDocument();
  });

  it("삭제 대상 미리보기는 DB 커서·snapshot을 유지하고 기존 시장 필터를 바꾸지 않는다", async () => {
    const user = userEvent.setup();
    const requested: Array<{ url: string; method: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      requested.push({ url, method: init?.method ?? "GET" });
      const common = commonResponse(url);
      if (common) return common;
      if (url === "/api/v1/credentials") return json({ items: [], storage: { available: null, confirmed: false, plaintext_fallback: false, safe_recheck_allowed: false } });
      if (url === "/api/v1/market-information/status") return json({ automatic_state: "STOPPED", refreshing: false, last_success_at: null, partial: false, providers: [{ provider_id: "fed", provider_name: "Fed", status: "OK" }] });
      if (url.startsWith("/api/v1/market-information/retention?")) {
        const parsed = new URL(url, "http://localhost");
        const pageTwo = parsed.searchParams.get("cursor") === "retention-next";
        return json({
          summary: { total_information_count: 2, total_source_record_count: 2, expired_count: 2, expiring_soon_count: 0, uncomputable_count: 0, checked_at: "2026-10-14T06:00:00Z" },
          items: [{ info_id: pageTwo ? "expired-2" : "expired-1", provider_id: "fed", title_original: pageTwo ? "Expired two" : "Expired one", first_collected_at: "2025-01-01T00:00:00Z", retention_expires_at: "2026-01-01T00:00:00Z", retention_status: "EXPIRED", first_collected_estimated: false }],
          next_cursor: pageTwo ? null : "retention-next", snapshot: "retention-snapshot", has_more: !pageTwo, deletion_enabled: false,
        });
      }
      if (url.startsWith("/api/v1/market-information?")) return json({ items: [], next_cursor: null, snapshot: "items", has_more: false });
      return json({}, 404);
    });
    renderMarket();
    await screen.findByText("Expired one");
    await user.click(screen.getByRole("button", { name: "경제" }));
    await user.selectOptions(screen.getByLabelText("출처"), "fed");
    await user.click(screen.getByRole("button", { name: "삭제 대상 더 보기" }));
    expect(await screen.findByText("Expired two")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "경제" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("출처")).toHaveValue("fed");
    const secondPage = requested.map((entry) => new URL(entry.url, "http://localhost")).find((url) => url.pathname.endsWith("/retention") && url.searchParams.get("cursor") === "retention-next");
    expect(secondPage?.searchParams.get("snapshot")).toBe("retention-snapshot");
    expect(secondPage?.searchParams.get("status")).toBe("EXPIRED");
    expect(requested.some((entry) => entry.method === "DELETE" && entry.url.includes("market-information"))).toBe(false);
  });
});

describe("시장정보 외부 링크 이중 검사", () => {
  it("공개 http/https만 허용하고 위험 프로토콜·userinfo·로컬 주소를 거부한다", () => {
    expect(safeExternalHref("https://www.sec.gov/news/press-release", "SAFE")).toBe("https://www.sec.gov/news/press-release");
    for (const value of ["javascript:alert(1)", "https://user@example.com/path", "http://localhost/path", "http://127.0.0.1/path", "http://[::1]/path", "http://[::ffff:127.0.0.1]/path", "https://example.com/public", "https://www.sec.gov.evil.example/path", "https://example.com/\u0000bad"]) {
      expect(safeExternalHref(value, "SAFE")).toBeNull();
    }
    expect(safeExternalHref("https://www.sec.gov/news", "UNSAFE")).toBeNull();
  });
});
