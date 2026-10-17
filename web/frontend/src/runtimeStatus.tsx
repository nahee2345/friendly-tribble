import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { displayScope, formatDateTime } from "./format";
import type { DashboardData, SystemStatus } from "./types";

interface RuntimeSnapshot {
  status: SystemStatus | null;
  dashboard: DashboardData | null;
  unavailable: boolean;
}

export function RuntimeStatusStrip() {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot>({ status: null, dashboard: null, unavailable: false });

  const refresh = useCallback(async () => {
    const [status, dashboard] = await Promise.allSettled([api.status(), api.dashboard()]);
    setSnapshot({
      status: status.status === "fulfilled" ? status.value : null,
      dashboard: dashboard.status === "fulfilled" ? dashboard.value : null,
      unavailable: status.status === "rejected" || dashboard.status === "rejected"
    });
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const { status, dashboard } = snapshot;
  const kis = status?.providers?.find((provider) => provider.provider.toLowerCase() === "kis");
  const orderLabel = status?.orders_enabled === false ? "주문 비활성" : "주문 상태 확인 필요";
  const modeLabel = status?.mode?.startsWith("LOCAL_WEB_") ? "LOCALHOST_ONLY" : status?.mode || "확인 필요";
  const databaseLabel = status?.database?.toUpperCase().includes("SQLITE") ? "SQLite" : status?.database || "확인 필요";

  return <section className={`runtime-strip ${dashboard?.data_scope === "SAMPLE" ? "sample" : ""}`} aria-label="현재 앱과 자료 상태" aria-live="polite">
    <span className="runtime-pill"><b>실행</b>{modeLabel}</span>
    <span className="runtime-pill"><b>저장</b>{databaseLabel}</span>
    <span className="runtime-pill"><b>자료</b>{displayScope(dashboard?.data_scope)}</span>
    <span className="runtime-pill"><b>기준</b>{formatDateTime(dashboard?.as_of)}{dashboard?.stale ? " · 미갱신" : ""}</span>
    <span className="runtime-pill"><b>예약</b>{status?.scheduler || "확인 필요"}</span>
    <span className="runtime-pill"><b>KIS</b>{kis?.status || "미연결/확인 필요"}</span>
    <span className={`runtime-pill ${status?.orders_enabled === false ? "safe" : "warning"}`}><b>권한</b>{orderLabel}</span>
    {snapshot.unavailable && <span className="runtime-error">일부 상태를 확인할 수 없습니다.</span>}
  </section>;
}

export function RuntimeBoundaryNotice() {
  return <section className="card runtime-boundary-card">
    <h2>로컬 실행 경계</h2>
    <dl className="runtime-boundary-list">
      <div><dt>Chrome 종료</dt><dd>화면과 페이지 열린 상태 알림은 종료됩니다. 사용자 백엔드 서비스가 실행 중이면 읽기 작업은 계속될 수 있습니다.</dd></div>
      <div><dt>로그아웃</dt><dd>현재 브라우저 세션만 종료됩니다. 예약 작업의 실제 상태는 자동화 화면에서 확인해야 합니다.</dd></div>
      <div><dt>백엔드 종료</dt><dd>자료 수집과 보고서 생성이 실행되지 않습니다.</dd></div>
      <div><dt>절전</dt><dd>절전 중에는 실행되지 않습니다. 복귀 뒤 작업 이력에서 놓친 작업의 복구 상태를 확인합니다.</dd></div>
      <div><dt>PC 종료</dt><dd>어떤 로컬 작업도 실행되지 않으며 클라우드 대체 실행을 제공하지 않습니다.</dd></div>
    </dl>
  </section>;
}
