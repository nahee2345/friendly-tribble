import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "wealthy_csrf=; Max-Age=0; path=/";
});

describe("API 보안 경계", () => {
  it("변경 요청에 same-origin 쿠키와 CSRF 헤더를 보낸다", async () => {
    document.cookie = "wealthy_csrf=test-token; path=/";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await api.logout();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/logout", expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-token");
  });

  it("서버 오류 본문이나 민감 입력을 화면 오류로 재출력하지 않는다", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "secret-raw-value" }), { status: 400 }));
    await expect(api.login("user", "secret-raw-value")).rejects.toEqual(expect.objectContaining({ message: "입력값을 확인해 주세요.", status: 400 }));
  });

  it("FastAPI 래퍼 응답을 화면 계약으로 안전하게 정규화한다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/reports")) return Promise.resolve(new Response(JSON.stringify({ items: [{ id: 1, created_at: "2026-10-11T07:00:00Z", data_cutoff_at: "2026-10-11T06:00:00Z", status: "COMPLETE" }] }), { status: 200 }));
      if (url.endsWith("/strategy-targets/current")) return Promise.resolve(new Response(JSON.stringify({ target: null }), { status: 200 }));
      if (url.endsWith("/strategy-catalog")) return Promise.resolve(new Response(JSON.stringify({ items: [{ id: "VALUE", name: "가치투자", criteria: "업종 내 가치평가", benefits: "가격 규율", risks: "가치 함정", required_data: "재무 자료" }] }), { status: 200 }));
      if (url.endsWith("/jobs")) return Promise.resolve(new Response(JSON.stringify({ items: [{ id: 1, kind: "collect", scheduled_for: "2026-10-11T06:00:00Z", state: "DONE" }] }), { status: 200 }));
      return Promise.resolve(new Response("{}", { status: 404 }));
    });
    expect((await api.reports())[0].as_of).toBe("2026-10-11T06:00:00Z");
    expect(await api.strategyCurrent()).toBeNull();
    expect((await api.strategyCatalog()).items[0].criteria).toEqual(["업종 내 가치평가"]);
    expect((await api.jobs())[0].status).toBe("DONE");
  });

  it("공급자 비밀은 values 본문으로만 전송하고 응답 원문을 요청하지 않는다", async () => {
    document.cookie = "wealthy_csrf=test-token; path=/";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ provider: "kis", environment: "paper", configured: true }), { status: 200 }));
    await api.saveCredential("kis", "paper", { app_key: "temporary", app_secret: "ephemeral" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ values: { app_key: "temporary", app_secret: "ephemeral" } });
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/credentials/kis/paper");
  });

  it("로컬 알림·포트폴리오·전략 이력을 상대 경로에서 읽는다", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/notifications")) return Promise.resolve(new Response(JSON.stringify({ items: [], browser_notifications: "PAGE_OPEN_ONLY" }), { status: 200 }));
      if (url.endsWith("/portfolio")) return Promise.resolve(new Response(JSON.stringify({ data_scope: "MISSING" }), { status: 200 }));
      if (url.endsWith("/strategy-targets/history")) return Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200 }));
      return Promise.resolve(new Response("{}", { status: 404 }));
    });
    await expect(api.notifications()).resolves.toEqual({ items: [], browser_notifications: "PAGE_OPEN_ONLY" });
    await expect(api.portfolio()).resolves.toEqual(expect.objectContaining({ accounts: [], positions: [], asset_class_totals: [], monthly_history: [] }));
    await expect(api.strategyHistory()).resolves.toEqual([]);
    for (const [url] of fetchMock.mock.calls) expect(String(url)).toMatch(/^\/api\/v1\//);
    expect("pushKey" in api).toBe(false);
    expect("addPushSubscription" in api).toBe(false);
    expect("deletePushSubscription" in api).toBe(false);
  });

  it("시장정보 필터를 제한된 query로 인코딩하고 수동 갱신에 CSRF를 보낸다", async () => {
    document.cookie = "wealthy_csrf=market-csrf; path=/";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.startsWith("/api/v1/market-information?")) return Promise.resolve(new Response(JSON.stringify({ items: [], next_cursor: null, snapshot: "s-1", has_more: false }), { status: 200 }));
      if (url === "/api/v1/market-information/refresh") return Promise.resolve(new Response(JSON.stringify({ status: "COMPLETED", new_items: 0, started_at: null, finished_at: null }), { status: 200 }));
      return Promise.resolve(new Response("{}", { status: 404 }));
    });

    await api.marketInformation({ q: "  Federal & Reserve  ", category: "ECONOMY", related: "RELATED", provider: "fed", sort: "PUBLISHED_DESC", cursor: "cursor-1", limit: 25, snapshot: "snapshot-1" });
    const listUrl = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost");
    expect(listUrl.pathname).toBe("/api/v1/market-information");
    expect(Object.fromEntries(listUrl.searchParams)).toEqual({ q: "Federal & Reserve", category: "ECONOMY", related: "RELATED", provider: "fed", sort: "PUBLISHED_DESC", cursor: "cursor-1", limit: "25", snapshot: "snapshot-1" });

    await api.refreshMarketInformation();
    const refreshInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/market-information/refresh");
    expect(refreshInit.method).toBe("POST");
    expect(new Headers(refreshInit.headers).get("X-CSRF-Token")).toBe("market-csrf");
    expect(refreshInit.credentials).toBe("same-origin");
  });
});
