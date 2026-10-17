/**
 * The former public-cloud PWA is intentionally inactive in the localhost app.
 * Keep cleanup narrowly scoped so unrelated service workers and caches survive.
 */
export async function cleanupLegacyWealthyPwa(): Promise<{ registrations: number; caches: number }> {
  let registrations = 0;
  let removedCaches = 0;

  if ("serviceWorker" in navigator) {
    const items = await navigator.serviceWorker.getRegistrations();
    for (const registration of items) {
      const scriptUrl = registration.active?.scriptURL
        ?? registration.waiting?.scriptURL
        ?? registration.installing?.scriptURL;
      if (!scriptUrl) continue;
      let script: URL;
      try { script = new URL(scriptUrl); } catch { continue; }
      if (script.origin !== window.location.origin || script.pathname !== "/sw.js") continue;
      if (await registration.unregister()) registrations += 1;
    }
  }

  if ("caches" in window) {
    const keys = await caches.keys();
    for (const key of keys) {
      if (!key.startsWith("wealthy-static-")) continue;
      if (await caches.delete(key)) removedCaches += 1;
    }
  }

  return { registrations, caches: removedCaches };
}

export type PageNotificationPermission = NotificationPermission | "unsupported";

export function pageNotificationPermission(): PageNotificationPermission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

export async function requestPageNotificationPermission(): Promise<PageNotificationPermission> {
  if (typeof Notification === "undefined") return "unsupported";
  // This function is only called from an explicit user button handler.
  return Notification.requestPermission();
}

export function showPageOpenNotification(id: string | number): boolean {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return false;
  try {
    // Keep browser notification UI generic; detailed text remains in the authenticated inbox.
    new Notification("Wealthy 알림", {
      body: "새 알림을 앱에서 확인해 주세요.",
      tag: `wealthy-local-${String(id).slice(0, 80)}`
    });
    return true;
  } catch {
    return false;
  }
}
