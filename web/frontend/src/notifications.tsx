import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { formatDateTime } from "./format";
import { showPageOpenNotification } from "./pwa";
import type { LocalNotificationFeed, LocalNotificationItem } from "./types";

const EMPTY_FEED: LocalNotificationFeed = {
  items: [],
  browser_notifications: "PAGE_OPEN_ONLY"
};

export function NotificationCenter() {
  const [feed, setFeed] = useState<LocalNotificationFeed>(EMPTY_FEED);
  const [open, setOpen] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const initialized = useRef(false);
  const knownIds = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      const next = await api.notifications();
      const safeItems = Array.isArray(next.items) ? next.items : [];
      if (initialized.current) {
        for (const item of safeItems) {
          const id = String(item.id);
          if (!knownIds.current.has(id)) showPageOpenNotification(item.id);
        }
      }
      knownIds.current = new Set(safeItems.map((item) => String(item.id)));
      initialized.current = true;
      setFeed({
        items: safeItems,
        browser_notifications: next.browser_notifications === "PAGE_OPEN_ONLY" ? "PAGE_OPEN_ONLY" : "PAGE_OPEN_ONLY"
      });
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 30_000);
    const onVisibility = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  const unread = feed.items.filter((item) => item.status.toUpperCase() !== "READ").length;

  return <div className="notification-center">
    <button
      className="icon-button notification-button"
      onClick={() => setOpen((value) => !value)}
      aria-expanded={open}
      aria-controls="notification-drawer"
      aria-label={`알림함 열기${unread ? `, 읽지 않음 ${unread}건` : ""}`}
    >
      <span aria-hidden="true">♢</span>{unread > 0 && <b>{unread > 99 ? "99+" : unread}</b>}
    </button>
    {open && <aside id="notification-drawer" className="notification-drawer" aria-label="웹 내부 알림함">
      <div className="card-title"><h2>알림함</h2><button className="text-button" onClick={() => setOpen(false)}>닫기</button></div>
      <p className="notification-boundary">브라우저 알림은 이 페이지가 열려 있을 때만 표시됩니다. 외부 Web Push는 사용하지 않습니다.</p>
      {unavailable
        ? <p className="message warning">알림 기록을 불러오지 못했습니다. 기존 화면 자료를 새 알림으로 바꾸지 않습니다.</p>
        : feed.items.length
          ? <ul>{feed.items.map((item) => <NotificationRow key={String(item.id)} item={item} />)}</ul>
          : <p className="empty">저장된 알림이 없습니다.</p>}
      <small>표시 모드: {feed.browser_notifications}</small>
    </aside>}
  </div>;
}

function NotificationRow({ item }: { item: LocalNotificationItem }) {
  return <li>
    <div><strong>{item.title || "Wealthy 알림"}</strong><span className="badge warning">{item.status || "확인 필요"}</span></div>
    <p>{item.message || "세부 내용이 없습니다."}</p>
    <small>{formatDateTime(item.created_at)} · {item.kind || "SYSTEM"}</small>
  </li>;
}
