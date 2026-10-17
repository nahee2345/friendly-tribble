import { afterEach, describe, expect, it, vi } from "vitest";
import { pageNotificationPermission, requestPageNotificationPermission, showPageOpenNotification } from "./pwa";

afterEach(() => vi.unstubAllGlobals());

describe("페이지 열린 상태 브라우저 알림", () => {
  it("사용자 동작 함수 호출 전에는 권한을 요청하지 않는다", async () => {
    const requestPermission = vi.fn().mockResolvedValue("granted");
    class FakeNotification {
      static permission: NotificationPermission = "default";
      static requestPermission = requestPermission;
    }
    vi.stubGlobal("Notification", FakeNotification);
    expect(pageNotificationPermission()).toBe("default");
    expect(requestPermission).not.toHaveBeenCalled();
    await expect(requestPageNotificationPermission()).resolves.toBe("granted");
    expect(requestPermission).toHaveBeenCalledOnce();
  });

  it("상세 금융 문자열 대신 고정된 일반 문구만 시스템 알림에 보낸다", () => {
    const calls: Array<{ title: string; options?: NotificationOptions }> = [];
    class FakeNotification {
      static permission: NotificationPermission = "granted";
      static requestPermission = vi.fn();
      constructor(title: string, options?: NotificationOptions) { calls.push({ title, options }); }
    }
    vi.stubGlobal("Notification", FakeNotification);
    expect(showPageOpenNotification("fixture-1")).toBe(true);
    expect(calls).toEqual([{ title: "Wealthy 알림", options: { body: "새 알림을 앱에서 확인해 주세요.", tag: "wealthy-local-fixture-1" } }]);
    expect(JSON.stringify(calls)).not.toMatch(/잔액|계좌번호|투자금액|token|secret/i);
  });

  it("권한이 없거나 API가 없으면 알림을 만들지 않는다", async () => {
    vi.stubGlobal("Notification", undefined);
    expect(pageNotificationPermission()).toBe("unsupported");
    await expect(requestPageNotificationPermission()).resolves.toBe("unsupported");
    expect(showPageOpenNotification("fixture-2")).toBe(false);
  });
});
