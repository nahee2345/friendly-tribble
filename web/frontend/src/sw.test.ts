import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanupLegacyWealthyPwa } from "./pwa";

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("레거시 PWA·Web Push 비활성화", () => {
  it("manifest를 연결하거나 새 service worker를 등록하지 않는다", () => {
    const index = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
    const main = readFileSync(resolve(process.cwd(), "src/main.tsx"), "utf8");
    expect(index).not.toMatch(/rel=["']manifest["']/i);
    expect(main).not.toContain("serviceWorker.register");
    expect(main).toContain("cleanupLegacyWealthyPwa");
  });

  it("레거시 worker는 캐시·fetch·push 기능 없이 자체 해제만 한다", () => {
    const path = resolve(process.cwd(), "public/sw.js");
    const source = readFileSync(path, "utf8");
    expect(source).toContain("self.registration.unregister()");
    expect(source).toContain('LEGACY_CACHE_PREFIX = "wealthy-static-"');
    expect(source).not.toContain('addEventListener("fetch"');
    expect(source).not.toContain('addEventListener("push"');
    expect(source).not.toContain("showNotification");
    expect(source).not.toContain("caches.open");
  });

  it("같은 출처 /sw.js와 Wealthy 캐시만 정리한다", async () => {
    const wealthy = { active: { scriptURL: `${window.location.origin}/sw.js` }, unregister: vi.fn().mockResolvedValue(true) };
    const unrelated = { active: { scriptURL: `${window.location.origin}/other-sw.js` }, unregister: vi.fn().mockResolvedValue(true) };
    const foreign = { active: { scriptURL: "https://example.test/sw.js" }, unregister: vi.fn().mockResolvedValue(true) };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { getRegistrations: vi.fn().mockResolvedValue([wealthy, unrelated, foreign]) }
    });
    const deleteCache = vi.fn().mockResolvedValue(true);
    vi.stubGlobal("caches", { keys: vi.fn().mockResolvedValue(["wealthy-static-v1", "unrelated-cache"]), delete: deleteCache });

    await expect(cleanupLegacyWealthyPwa()).resolves.toEqual({ registrations: 1, caches: 1 });
    expect(wealthy.unregister).toHaveBeenCalledOnce();
    expect(unrelated.unregister).not.toHaveBeenCalled();
    expect(foreign.unregister).not.toHaveBeenCalled();
    expect(deleteCache).toHaveBeenCalledWith("wealthy-static-v1");
    expect(deleteCache).not.toHaveBeenCalledWith("unrelated-cache");
  });
});
