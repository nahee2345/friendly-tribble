import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, subscribeAuthInvalidation } from "./api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((ready) => { resolve = ready; });
  return { promise, resolve };
}
function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" }
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("P0 authenticated response isolation", () => {
  it("does not expose a delayed old-user asset response after logout", async () => {
    const oldPortfolio = deferred<Response>();
    const fetch = vi.fn((path: string) =>
      path === "/api/v1/portfolio"
        ? oldPortfolio.promise
        : Promise.resolve(new Response(null, { status: 204 }))
    );
    vi.stubGlobal("fetch", fetch);
    const oldRequest = api.portfolio();
    const logout = api.logout();
    oldPortfolio.resolve(json({
      data_scope: "MANUAL_ACTUAL", investable_total_krw: "987654321",
      accounts: [{id:"private-old-user"}], positions: [],
      asset_class_totals: [], monthly_history: []
    }));
    await expect(oldRequest).rejects.toMatchObject({ status: 401 });
    await logout;
  });

  it("revokes all in-flight owner-scoped requests on 401", async () => {
    const slow = deferred<Response>();
    let shouldRevoke = true;
    const onRevoke = vi.fn();
    const stop = subscribeAuthInvalidation(onRevoke);
    vi.stubGlobal("fetch", vi.fn((path: string) =>
      path === "/api/v1/portfolio"
        ? slow.promise
        : Promise.resolve(shouldRevoke ? json({ error: "expired" }, 401) : json({ authenticated: false }))
    ));
    const oldRequest = api.portfolio();
    const unauthorized = api.session();
    await expect(unauthorized).rejects.toBeInstanceOf(ApiError);
    expect(onRevoke).toHaveBeenCalledTimes(1);
    shouldRevoke = false;
    slow.resolve(json({
      data_scope: "SAMPLE", investable_total_krw: "1",
      accounts: [], positions: [], asset_class_totals: [], monthly_history: []
    }));
    await expect(oldRequest).rejects.toMatchObject({ status: 401 });
    stop();
  });

  it("invalidates earlier assets even when the next account logs in", async () => {
    const slow = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((path: string) =>
      path === "/api/v1/portfolio"
        ? slow.promise
        : Promise.resolve(json({ user: { id: "another-user", username: "new-user" } }))
    ));
    const oldRequest = api.portfolio();
    await api.login("new-user", "synthetic-only");
    slow.resolve(json({
      data_scope: "MANUAL_ACTUAL", investable_total_krw: "1000",
      accounts: [{ id: "prior-owner" }], positions: [],
      asset_class_totals: [], monthly_history: []
    }));
    await expect(oldRequest).rejects.toMatchObject({ status: 401 });
  });
});
