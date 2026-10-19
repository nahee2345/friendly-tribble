import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "./api";
import { safeCatalogItem } from "./catalog";
import { ManualAssetsPanel, ManualAssetsSummary } from "./ManualAssetsPanel";
import { sliderValue, totalGuidance } from "./decimal";
import { displayScope, formatDateTime, formatKrw } from "./format";
import { useLoad } from "./hooks";
import { MarkdownReport } from "./MarkdownReport";
import { pageNotificationPermission, requestPageNotificationPermission } from "./pwa";
import { RuntimeBoundaryNotice } from "./runtimeStatus";
import { safeExternalHref } from "./marketLinks";
import { useStrategyDraft } from "./strategyDraft";
import {
  TARGET_STRATEGY_IDS,
  type CredentialOverview,
  type CredentialStatus,
  type DashboardData,
  type MarketInformationCategory,
  type MarketInformationItem,
  type MarketInformationProviderStatus,
  type MarketInformationQuery,
  type MarketInformationRelatedFilter,
  type MarketInformationRetentionItem,
  type MarketInformationRetentionResponse,
  type MarketInformationSort,
  type MarketInformationSource,
  type MarketInformationStatus,
  type PortfolioData,
  type ReportDetail,
  type StrategyCatalogItem,
  type StrategyPreview,
  type StrategyTargetVersion,
  type TargetStrategyId
} from "./types";

function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) {
  return <header className="page-heading">
    <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{description}</p></div>
    {actions && <div className="heading-actions">{actions}</div>}
  </header>;
}

function LoadMessage({ loading, error }: { loading: boolean; error: string }) {
  if (loading) return <div className="message neutral" role="status">자료를 불러오는 중입니다…</div>;
  if (error) return <div className="message warning" role="alert">{error} 기존 결과를 새 자료로 표시하지 않습니다.</div>;
  return null;
}

function StatusBadge({ value }: { value?: string | null }) {
  const normalized = value?.toLowerCase() ?? "unknown";
  const kind = ["ok", "ready", "success", "connected", "completed", "direct", "done"].some((part) => normalized.includes(part)) ? "success"
    : ["fail", "error", "unavailable", "missing"].some((part) => normalized.includes(part)) ? "danger" : "warning";
  return <span className={`badge ${kind}`}>{value || "확인 필요"}</span>;
}

export function DashboardPage() {
  const state = useLoad(api.dashboard, []);
  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const data = state.data;

  async function loadSample() {
    setBusy(true); setActionMessage("");
    try { await api.loadSample(); setActionMessage("샘플 자료를 불러왔습니다. 화면 전체에서 SAMPLE로 구분됩니다."); state.reload(); }
    catch { setActionMessage("샘플 자료를 불러오지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true); setActionMessage("");
    try { await api.uploadScenario(file); setActionMessage("자료를 검증해 불러왔습니다."); state.reload(); }
    catch (reason) { setActionMessage(reason instanceof ApiError ? reason.message : "자료를 불러오지 못했습니다. 이전 결과는 유지하지 않습니다."); }
    finally { setBusy(false); }
  }

  return <>
    <PageHeader eyebrow="OVERVIEW" title="통합 대시보드" description="자산, 자료 기준시각, 위험 경고와 자동화 상태를 한곳에서 확인합니다."
      actions={<><button className="secondary" onClick={() => fileRef.current?.click()} disabled={busy}>JSON 자료 열기</button><button className="primary" onClick={loadSample} disabled={busy}>샘플로 시작</button><input className="visually-hidden" ref={fileRef} type="file" accept="application/json,.json" onChange={upload} /></>} />
    <LoadMessage loading={state.loading} error={state.error} />
    {actionMessage && <p className="message neutral" role="status">{actionMessage}</p>}
    {data && <>
      <div className={`scope-banner ${data.data_scope === "SAMPLE" ? "sample" : ""}`}>
        <strong>{displayScope(data.data_scope)}</strong><span>기준 {formatDateTime(data.as_of)}</span>{data.stale && <span className="badge warning">미갱신</span>}
      </div>
      <section className="metric-grid" aria-label="자산 요약">
        <article className="metric"><span>투자자산 합계</span><strong>{formatKrw(data.investable_total_krw)}</strong><small>결측값을 0원으로 바꾸지 않습니다.</small></article>
        <article className="metric"><span>초기자산 선언값</span><strong>{formatKrw(data.declared_initial_assets_krw)}</strong><small>상세 원장 합계와 별도로 대조합니다.</small></article>
        <article className="metric"><span>자료 기준시각</span><strong className="metric-time">{formatDateTime(data.as_of)}</strong><small>수집 {formatDateTime(data.collected_at)}</small></article>
        <article className="metric"><span>최근 보고서</span><strong className="metric-time">{data.latest_report ? formatDateTime(data.latest_report.created_at) : "없음"}</strong><small>{data.latest_report?.status ?? "생성된 보고서가 없습니다."}</small></article>
      </section>
      <div className="dashboard-grid">
        <section className="card span-2"><div className="card-title"><h2>주요 경고</h2><span>{data.warnings?.length ?? 0}건</span></div>{data.warnings?.length ? <ul className="alert-list">{data.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul> : <p className="empty">현재 자료에 표시할 경고가 없습니다. 이는 시장 위험이 없다는 뜻이 아닙니다.</p>}</section>
        <section className="card"><div className="card-title"><h2>공급자 상태</h2><Link to="/automation">자세히</Link></div>{data.providers?.length ? <ul className="status-list">{data.providers.map((provider) => <li key={provider.provider}><span>{provider.provider}</span><StatusBadge value={provider.status} /></li>)}</ul> : <p className="empty">연결된 공급자 상태가 없습니다.</p>}</section>
        <section className="card"><div className="card-title"><h2>운영 경계</h2></div><ul className="boundary-list"><li>분석·보고서만 지원</li><li>주문 기능 없음</li><li>사용자 승인 없는 거래 없음</li></ul></section>
      </div>
    </>}
  </>;
}

export function AssetsPage() {
  const state = useLoad(api.portfolio, []);
  return <>
    <PageHeader eyebrow="PORTFOLIO" title="내 자산 · 월별 정리" description="현재 인증된 사용자의 검증된 원장과 월별 스냅샷만 표시합니다." />
    <LoadMessage loading={state.loading} error={state.error} />
    <ManualAssetsSummary />
    {state.data && <>
      <div className={`scope-banner ${state.data.data_scope === "SAMPLE" ? "sample" : ""}`}><strong>{displayScope(state.data.data_scope)}</strong><span>기준 {formatDateTime(state.data.as_of)}</span>{state.data.stale && <span className="badge warning">미갱신</span>}</div>
      <section className="metric-grid portfolio-metrics" aria-label="포트폴리오 요약">
        <article className="metric"><span>투자자산 합계</span><strong>{formatKrw(state.data.investable_total_krw)}</strong><small>결측값과 보호저축을 임의로 합산하지 않습니다.</small></article>
        <article className="metric"><span>계좌 원장</span><strong>{state.data.accounts.length ? `${state.data.accounts.length}개` : "미입력"}</strong><small>서로 다른 자료 영역은 합산하지 않습니다.</small></article>
        <article className="metric"><span>보유 항목</span><strong>{state.data.positions.length ? `${state.data.positions.length}개` : "미입력"}</strong><small>검증된 원장 행만 집계합니다.</small></article>
        <article className="metric"><span>월별 기록</span><strong>{state.data.monthly_history.length ? `${state.data.monthly_history.length}개월` : "미입력"}</strong><small>입금과 투자손익을 구분합니다.</small></article>
      </section>
      <div className="portfolio-grid">
        <AllocationChart portfolio={state.data} />
        <MonthlyHistoryTable portfolio={state.data} />
      </div>
      <PositionsTable portfolio={state.data} />
    </>}
  </>;
}

function safePercentWidth(value?: string | null): number | null {
  if (!value || !/^\d+(?:\.\d+)?$/.test(value)) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 100 ? number : null;
}

function AllocationChart({ portfolio }: { portfolio: PortfolioData }) {
  return <section className="card"><div className="card-title"><h2>위험 자산군 현황</h2><span>서버 계산 비중</span></div>
    {portfolio.asset_class_totals.length ? <div className="allocation-chart" role="img" aria-label="자산군별 실제 비중">
      {portfolio.asset_class_totals.map((entry) => {
        const width = safePercentWidth(entry.weight_percent);
        return <div className="allocation-row" key={entry.asset_class}>
          <div><strong>{entry.asset_class}</strong><span>{entry.weight_percent != null ? `${entry.weight_percent}%` : "비중 계산 불가"}</span></div>
          <div className="allocation-track" aria-hidden="true">{width != null && <span style={{ width: `${width}%` }} />}</div>
          <small>{formatKrw(entry.amount_krw)} · {entry.status || "상태 확인 필요"}</small>
        </div>;
      })}
    </div> : <p className="empty">자산군 합계가 없습니다. 0%로 간주하지 않습니다.</p>}
  </section>;
}

function MonthlyHistoryTable({ portfolio }: { portfolio: PortfolioData }) {
  return <section className="card"><div className="card-title"><h2>월별 자산 기록</h2><span>{portfolio.monthly_history.length ? `${portfolio.monthly_history.length}건` : "자료 없음"}</span></div>
    {portfolio.monthly_history.length ? <div className="table-wrap"><table><thead><tr><th>월</th><th>투자자산</th><th>순납입</th><th>투자손익</th><th>기준시각</th></tr></thead><tbody>{portfolio.monthly_history.map((point) => <tr key={point.month}><td>{point.month}</td><td>{formatKrw(point.investable_total_krw)}</td><td>{formatKrw(point.net_contributions_krw)}</td><td>{formatKrw(point.investment_pnl_krw)}</td><td>{formatDateTime(point.as_of)}</td></tr>)}</tbody></table></div> : <p className="empty">월별 스냅샷이 없습니다. 최신 자산을 과거 이력으로 복제하지 않습니다.</p>}
  </section>;
}

function PositionsTable({ portfolio }: { portfolio: PortfolioData }) {
  return <section className="card portfolio-positions"><div className="card-title"><h2>보유 원장</h2><span>{portfolio.positions.length ? `${portfolio.positions.length}건` : "미입력"}</span></div>
    {portfolio.positions.length ? <div className="table-wrap"><table><thead><tr><th>종목</th><th>식별자</th><th>유형</th><th>시장·통화</th><th>수량</th><th>평가액</th><th>기준시각</th></tr></thead><tbody>{portfolio.positions.map((position) => <tr key={String(position.id)}><td>{position.name || "이름 미입력"}</td><td>{position.instrument_id}</td><td>{position.instrument_type || position.asset_class || "확인 필요"}</td><td>{[position.market, position.currency].filter(Boolean).join(" · ") || "확인 필요"}</td><td>{position.quantity ?? "미입력"}</td><td>{formatKrw(position.market_value_krw)}</td><td>{formatDateTime(position.as_of)}</td></tr>)}</tbody></table></div> : <p className="empty">보유 원장 자료가 없습니다. 샘플 종목을 실제 보유로 표시하지 않습니다.</p>}
  </section>;
}

interface CapabilityProps { title: string; description: string; prerequisites: string[]; status?: string; children?: ReactNode }
export function CapabilityPage({ title, description, prerequisites, status = "미구현", children }: CapabilityProps) {
  return <><PageHeader title={title} description={description} /><section className="card capability"><div className="card-title"><h2>현재 상태</h2><StatusBadge value={status} /></div>{children}<h3>사용 전 필요한 조건</h3><ul>{prerequisites.map((item) => <li key={item}>{item}</li>)}</ul><p className="message warning">조건을 확인하기 전에는 결과를 만들거나 실행 가능한 것처럼 표시하지 않습니다.</p></section></>;
}

export function StrategyMixPage() {
  const draft = useStrategyDraft();
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [history, setHistory] = useState<StrategyTargetVersion[] | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [preview, setPreview] = useState<StrategyPreview | null>(null);
  const [version, setVersion] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const guidance = useMemo(() => totalGuidance(TARGET_STRATEGY_IDS.map((id) => draft.weights[id])), [draft.weights]);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.strategyCurrent(),
      api.strategyCatalog().catch(() => ({ items: [] })),
      api.strategyHistory().catch(() => null),
      api.portfolio().catch(() => null)
    ])
      .then(([current, remote, storedHistory, currentPortfolio]) => {
        if (active) {
          draft.hydrate(current);
          setCatalog(remote.items);
          setHistory(storedHistory);
          setPortfolio(currentPortfolio);
        }
      })
      .catch(() => { if (active) setMessage("저장된 전략을 불러오지 못했습니다."); });
    return () => { active = false; };
    // 초안은 화면 밖에서도 유지되므로 최초 마운트에서만 불러온다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setPreview(null);
    if (!guidance.valid) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api.strategyPreview(draft.weights).then((result) => { if (active) setPreview(result); }).catch(() => { if (active) setMessage("서버 미리보기를 확인하지 못했습니다."); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [draft.weights, guidance.valid]);

  async function save(event: FormEvent) {
    event.preventDefault(); setMessage("");
    if (!guidance.valid || !preview?.valid || !version.trim()) return;
    setBusy(true);
    try {
      const saved = await api.saveStrategy(version.trim(), draft.weights);
      draft.saved(saved); setHistory((items) => items ? [saved, ...items.filter((item) => item.id !== saved.id)] : [saved]); setVersion(""); setMessage("새 전략 목표 버전을 저장했습니다. 기존 버전은 변경하지 않았습니다.");
    } catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "전략 버전을 저장하지 못했습니다."); }
    finally { setBusy(false); }
  }

  return <>
    <PageHeader eyebrow="TARGET ALLOCATION" title="투자전략 혼합 설정" description="전체 투자자산을 어떤 투자 접근에 배분할지 목표를 정합니다."
      actions={<><div className={`total-chip ${guidance.valid ? "valid" : "invalid"}`}><small>전략 합계</small><strong>{guidance.total ?? "—"}% / 100%</strong></div><button className="secondary" onClick={draft.restore} disabled={!draft.lastSaved}>비중 복원</button></>} />
    <p className="policy-banner"><strong>사용자 목표 전략 배분입니다.</strong> 후보 평가용 점수 가중치와 다르며, 실제 자산군 배분·매수 주문을 바꾸거나 위험 한도를 우회하지 않습니다.</p>
    <StrategyStateOverview draft={draft.weights} draftTotal={guidance.total} dirty={draft.dirty} saved={draft.lastSaved} portfolio={portfolio} catalog={catalog} />
    <form onSubmit={save} className="strategy-layout">
      <section className="card strategy-editor" aria-labelledby="mix-title">
        <div className="card-title"><h2 id="mix-title">전략별 목표 비중</h2><span>{draft.dirty ? "저장하지 않은 초안" : "마지막 저장 상태"}</span></div>
        <div className="strategy-rows">
          {TARGET_STRATEGY_IDS.map((id) => {
            const item = safeCatalogItem(id, catalog.find((entry) => entry.id === id));
            return <article className="strategy-row" key={id}>
              <div className="strategy-row-head"><Link to={`/strategies/${id}`}><strong>{item.name}</strong></Link><span>{item.status || "상태 확인 필요"}</span></div>
              <div className="strategy-controls">
                <input aria-label={`${item.name} 슬라이더`} type="range" min="0" max="100" step="0.1" value={sliderValue(draft.weights[id])} onChange={(event) => draft.setWeight(id, event.target.value)} />
                <label><span className="visually-hidden">{item.name} 비중</span><input inputMode="decimal" value={draft.weights[id]} onChange={(event) => draft.setWeight(id, event.target.value)} aria-invalid={guidance.total === null} /><b>%</b></label>
              </div>
              <p className="target-amount">목표 금액 <strong>{formatKrw(preview?.target_amounts_krw?.[id])}</strong></p>
            </article>;
          })}
        </div>
        <div className={`message ${guidance.valid ? "success" : "warning"}`} role="status">{guidance.message}</div>
        <div className="save-row"><label>새 버전 이름<input value={version} onChange={(e) => setVersion(e.target.value)} maxLength={80} placeholder="예: 2026-10 목표" /></label><button className="primary" type="submit" disabled={busy || !guidance.valid || !preview?.valid || !version.trim()}>새 불변 버전 저장</button></div>
        {message && <p className="message neutral" role="status">{message}</p>}
      </section>
      <aside className="card sticky-summary"><h2>전략 목표 요약</h2><dl><div><dt>저장 버전</dt><dd>{draft.lastSaved?.version ?? "없음"}</dd></div><div><dt>저장 시각</dt><dd>{formatDateTime(draft.lastSaved?.created_at)}</dd></div><div><dt>총 투자자산</dt><dd>{formatKrw(preview?.total_assets_krw)}</dd></div></dl><h3>주의</h3><p>목표 금액은 서버가 확인한 투자자산이 있을 때만 계산합니다. 보호저축·비상금과 결측 자산은 임의로 더하지 않습니다.</p><Link className="text-link" to="/strategy-library">8개 전략 상세 보기 →</Link></aside>
    </form>
    <div className="strategy-history-grid">
      <StrategyHistory history={history} />
      <TransitionStatus portfolio={portfolio} />
    </div>
  </>;
}

function StrategyStateOverview({
  draft,
  draftTotal,
  dirty,
  saved,
  portfolio,
  catalog
}: {
  draft: Record<TargetStrategyId, string>;
  draftTotal: string | null;
  dirty: boolean;
  saved: StrategyTargetVersion | null;
  portfolio: PortfolioData | null;
  catalog: StrategyCatalogItem[];
}) {
  const actual = portfolio?.actual_strategy;
  return <section className="strategy-state-grid" aria-label="전략 초안, 저장 목표와 실제 비중 구분">
    <article className="card state-card"><span className="eyebrow">DRAFT</span><h2>편집 초안</h2><strong>{draftTotal != null ? `${draftTotal}%` : "입력 오류"}</strong><p>{dirty ? "저장하지 않은 변경이 있습니다." : "마지막 저장 목표와 같은 편집 상태입니다."}</p><StrategyWeightSummary weights={draft} catalog={catalog} /></article>
    <article className="card state-card"><span className="eyebrow">SAVED TARGET</span><h2>저장 목표</h2><strong>{saved?.version || "저장 버전 없음"}</strong><p>{saved ? formatDateTime(saved.created_at) : "정확히 100%인 새 불변 버전을 저장해 주세요."}</p>{saved && <StrategyWeightSummary weights={saved.weights} catalog={catalog} />}</article>
    <article className="card state-card"><span className="eyebrow">ACTUAL</span><h2>실제 비중</h2><strong>{actual?.status || "계산 불가"}</strong><p>{actual?.missing_reason || (actual?.weights ? `기준 ${formatDateTime(actual.as_of)}` : "보유자산의 전략 귀속 자료가 미입력입니다.")}</p>{actual?.weights ? <StrategyWeightSummary weights={actual.weights} catalog={catalog} /> : <p className="empty">목표 비중을 실제 비중처럼 표시하지 않습니다.</p>}<small>중복 귀속 검사: {actual?.double_count_checked === true ? "완료" : "확인 필요"}</small></article>
  </section>;
}

function StrategyWeightSummary({ weights, catalog }: { weights: Partial<Record<TargetStrategyId, string>>; catalog: StrategyCatalogItem[] }) {
  const values = TARGET_STRATEGY_IDS.filter((id) => weights[id] != null);
  return values.length ? <ul className="compact-weights">{values.map((id) => <li key={id}><span>{safeCatalogItem(id, catalog.find((item) => item.id === id)).name}</span><b>{weights[id]}%</b></li>)}</ul> : <p className="empty">표시할 비중이 없습니다.</p>;
}

function StrategyHistory({ history }: { history: StrategyTargetVersion[] | null }) {
  return <section className="card"><div className="card-title"><h2>저장 목표 변경 이력</h2><span>추가 전용</span></div>{history === null
    ? <p className="empty">변경 이력을 불러올 수 없습니다.</p>
    : history.length
      ? <div className="table-wrap"><table><thead><tr><th>버전</th><th>저장 시각</th><th>상태</th></tr></thead><tbody>{history.map((item) => <tr key={String(item.id ?? `${item.version}-${item.created_at}`)}><td>{item.version}</td><td>{formatDateTime(item.created_at)}</td><td>불변 저장</td></tr>)}</tbody></table></div>
      : <p className="empty">저장된 목표 버전이 없습니다.</p>}</section>;
}

function TransitionStatus({ portfolio }: { portfolio: PortfolioData | null }) {
  const actual = portfolio?.actual_strategy;
  return <section className="card"><div className="card-title"><h2>점진적 포트폴리오 전환</h2><StatusBadge value={actual?.transition_status || "설정 필요"} /></div><p>{actual?.transition_summary || (actual?.weights ? "사용자가 확정한 전환 계획이 없습니다." : "실제 전략 귀속 비중이 없어 전환량을 계산할 수 없습니다.")}</p><dl className="facts"><div><dt>실제 비중 기준</dt><dd>{formatDateTime(actual?.as_of)}</dd></div><div><dt>다음 검토</dt><dd>{formatDateTime(actual?.next_review_at)}</dd></div></dl><p className="message warning">시장 수익률이나 날짜를 임의로 가정해 완료일·매수 수량을 만들지 않습니다.</p></section>;
}

export function StrategyLibraryPage() {
  const state = useLoad(api.strategyCatalog, []);
  return <><PageHeader eyebrow="STRATEGY LIBRARY" title="8개 투자전략" description="현재 엔진 재사용 여부, 필요한 자료와 평가 가능 상태를 그대로 확인합니다." /><LoadMessage loading={state.loading} error={state.error} /><div className="catalog-grid">{TARGET_STRATEGY_IDS.map((id) => { const item = safeCatalogItem(id, state.data?.items.find((entry) => entry.id === id)); return <Link to={`/strategies/${id}`} className="catalog-card" key={id}><span className="catalog-index">{String(TARGET_STRATEGY_IDS.indexOf(id) + 1).padStart(2, "0")}</span><h2>{item.name}</h2><p>{item.description}</p><StatusBadge value={item.status} /><span className="text-link">상세 확인 →</span></Link>; })}</div></>;
}

export function StrategyDetailPage() {
  const { strategyId } = useParams();
  const id = TARGET_STRATEGY_IDS.includes(strategyId as TargetStrategyId) ? strategyId as TargetStrategyId : null;
  const state = useLoad(api.strategyCatalog, []);
  if (!id) return <CapabilityPage title="전략을 찾을 수 없습니다" description="지원되는 8개 전략에서 다시 선택해 주세요." prerequisites={["전략 혼합 화면에서 전략명 선택"]} />;
  const item = safeCatalogItem(id, state.data?.items.find((entry) => entry.id === id));
  return <><PageHeader eyebrow="STRATEGY DETAIL" title={item.name} description={item.description || "설명 자료가 없습니다."} actions={<Link className="button secondary" to="/strategies">혼합 설정으로 돌아가기</Link>} /><LoadMessage loading={state.loading} error={state.error} /><div className="detail-grid"><section className="card"><h2>투자 철학과 선정 기준</h2><p>{item.philosophy || "검증된 설명이 아직 연결되지 않았습니다."}</p><h3>선정 기준</h3>{item.criteria?.length ? <ul>{item.criteria.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="empty">평가 기준 연결 필요</p>}<h3>기존 엔진 대응</h3><p>{item.mapping || "대응 상태 확인 필요"}</p></section><section className="card"><h2>장점과 위험</h2><div className="pros-cons"><div><h3>검토 장점</h3>{item.benefits?.length ? <ul>{item.benefits.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="empty">자료 없음</p>}</div><div><h3>주의·위험</h3>{item.risks?.length ? <ul>{item.risks.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="empty">자료 없음</p>}</div></div></section><aside className="card"><h2>필요 자료 상태</h2>{item.required_data?.length ? <ul className="status-list">{item.required_data.map((entry) => <li key={entry.name}><span>{entry.name}</span><StatusBadge value={entry.status} /></li>)}</ul> : <p className="empty">서버 카탈로그에 자료 요건이 없습니다.</p>}<p className="message warning">자료가 없으면 적합성·추천종목·전망을 만들지 않습니다.</p></aside></div></>;
}

export function ReportsPage() {
  const list = useLoad(api.reports, []);
  const [selected, setSelected] = useState<ReportDetail | null>(null);
  const [error, setError] = useState("");
  async function open(id: string | number) { setError(""); setSelected(null); try { setSelected(await api.report(id)); } catch { setError("보고서를 불러오지 못했습니다."); } }
  const generationLabel = (value?: string | null) => value === "MANUAL" ? "수동 생성" : value === "SCHEDULED" ? "예약 생성" : "확인 불가";
  const scopeLabel = (value?: string | null) => value ? displayScope(value) : "확인 불가";
  return <><PageHeader eyebrow="REPORTS" title="일간 · 월간 보고서" description="보고서 버전과 자료 기준시각을 보존해 표시합니다." /><LoadMessage loading={list.loading} error={list.error} /><div className="report-layout"><section className="card report-list"><h2>보고서 이력</h2>{list.data?.length ? <ul>{list.data.map((report) => <li key={report.id}><button onClick={() => open(report.id)}><span><strong>{generationLabel(report.generation_kind)}</strong><small>{formatDateTime(report.created_at)} · {scopeLabel(report.data_scope)}</small></span><StatusBadge value={report.status} /></button></li>)}</ul> : <p className="empty">생성된 보고서가 없습니다.</p>}</section><section className="card report-viewer" aria-live="polite"><div className="card-title"><h2>보고서 보기</h2>{selected && <span>{formatDateTime(selected.as_of)}</span>}</div>{error && <p className="message error">{error}</p>}{selected ? <MarkdownReport markdown={selected.markdown} /> : <p className="empty">왼쪽에서 보고서를 선택하세요. 보고서 본문은 HTML로 실행하지 않고 안전한 텍스트로 표시합니다.</p>}</section></div></>;
}

export function AutomationPage() {
  const jobs = useLoad(api.jobs, []);
  const status = useLoad(api.status, []);
  return <><PageHeader eyebrow="AUTOMATION" title="자동 수집 · 예약 관리" description="Asia/Seoul 15:00 수집과 16:00 보고서 작업의 실제 로컬 실행 기록을 표시합니다." /><LoadMessage loading={jobs.loading || status.loading} error={jobs.error || status.error} /><div className="card-grid"><section className="card"><h2>사용자 예약 상태</h2><dl className="facts"><div><dt>시간대</dt><dd>{status.data?.timezone || "확인 필요"}</dd></div><div><dt>15시 수집</dt><dd>{formatDateTime(status.data?.next_collection_at)}</dd></div><div><dt>16시 보고서</dt><dd>{formatDateTime(status.data?.next_report_at)}</dd></div><div><dt>스케줄러</dt><dd><StatusBadge value={status.data?.scheduler} /></dd></div></dl><p className="message warning">백엔드 또는 PC가 중지·절전 상태이면 실행되지 않습니다. 공급자 미연결 상태를 수집 완료로 표시하지 않습니다.</p></section><section className="card span-2"><h2>최근 작업과 복구 상태</h2>{jobs.data?.length ? <div className="table-wrap"><table><thead><tr><th>작업</th><th>예약 시각</th><th>상태</th><th>완료 시각</th></tr></thead><tbody>{jobs.data.map((job) => <tr key={job.id}><td>{job.kind}</td><td>{formatDateTime(job.scheduled_for)}</td><td><StatusBadge value={job.status} /></td><td>{formatDateTime(job.finished_at)}</td></tr>)}</tbody></table></div> : <p className="empty">저장된 작업 이력이 없습니다. 실행되지 않은 작업을 성공으로 표시하지 않습니다.</p>}</section></div><RuntimeBoundaryNotice /></>;
}

function storageUnavailable(overview: CredentialOverview | null): boolean {
  const storage = overview?.storage;
  if (!storage) return false;
  return storage.confirmed === true && (
    storage.available === false || storage.state === "UNAVAILABLE_CONFIRMED"
  );
}

export function ProviderSettingsPage() {
  const statuses = useLoad(api.credentials, []);
  const [environment, setEnvironment] = useState("paper");
  const [appKey, setAppKey] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const current = statuses.data?.items.find((item) => item.provider === "kis" && item.environment === environment);
  const unavailable = storageUnavailable(statuses.data);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try { await api.saveCredential("kis", environment, { app_key: appKey, app_secret: appSecret }); setMessage("로컬 보안 저장소의 인증정보를 교체했습니다. 원문은 다시 조회할 수 없습니다."); statuses.reload(); }
    catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "인증정보를 저장하지 못했습니다."); }
    finally { setAppKey(""); setAppSecret(""); setBusy(false); }
  }
  async function remove() {
    setBusy(true); setMessage("");
    try { await api.deleteCredential("kis", environment); setMessage("저장된 인증정보를 삭제했습니다."); statuses.reload(); }
    catch { setMessage("인증정보를 삭제하지 못했습니다."); }
    finally { setBusy(false); }
  }
  return <>
    <PageHeader eyebrow="PROVIDER SETTINGS" title="KIS API 설정" description="사용자 본인의 읽기 전용 시세 수집 인증정보만 이 PC의 보안 저장 경로에 등록합니다." />
    <LoadMessage loading={statuses.loading} error={statuses.error} />
    {unavailable && <section className="message error secure-storage-unavailable" role="alert" aria-labelledby="secure-storage-unavailable-title">
      <strong id="secure-storage-unavailable-title">보안 저장소 사용 불가</strong>
      <span>백엔드가 현재 사용할 수 없음을 확인했습니다. 원인은 단정하지 않으며 평문 저장으로 대체하지 않습니다.</span>
      <small>마지막 확인 {formatDateTime(statuses.data?.storage?.checked_at)}</small>
    </section>}
    <div className="settings-grid">
      <form className="card stack-lg" onSubmit={save} autoComplete="off">
        <div className="card-title"><h2>인증정보 등록 · 교체</h2><StatusBadge value={current?.configured ? "설정됨" : "미설정"} /></div>
        <label>환경<select value={environment} onChange={(e) => { setEnvironment(e.target.value); setAppKey(""); setAppSecret(""); }}><option value="paper">모의 환경</option><option value="live">실전 시세 환경</option></select></label>
        <label>App Key<input type="password" autoComplete="off" spellCheck={false} value={appKey} onChange={(e) => setAppKey(e.target.value)} required maxLength={256} disabled={unavailable} /></label>
        <label>App Secret<input type="password" autoComplete="off" spellCheck={false} value={appSecret} onChange={(e) => setAppSecret(e.target.value)} required maxLength={256} disabled={unavailable} /></label>
        <p className="security-note">브라우저 저장소·로그·보고서에 원문을 저장하지 않습니다. OS 보안 저장소 또는 DB 밖 master key를 사용할 수 없으면 평문으로 대체하지 않고 저장에 실패합니다. 자동 상태 조회는 보안 저장소를 다시 열지 않습니다.</p>
        <div className="button-row"><button className="primary" disabled={busy || unavailable}>암호화 저장</button><button className="danger-button" type="button" onClick={remove} disabled={busy || unavailable || !current?.configured}>등록 삭제</button></div>
        {message && <p className="message neutral" role="status">{message}</p>}
      </form>
      <section className="card"><h2>권한 경계</h2><ul className="boundary-list"><li>시세·거래량 수집 전용</li><li>계좌 조회 기능 없음</li><li>주문 코드와 연결하지 않음</li><li>연결 검사는 명시적 읽기 전용 요청만 사용</li></ul><h3>최근 상태</h3><dl className="facts"><div><dt>연결</dt><dd>{current?.connection_status || "검사하지 않음"}</dd></div><div><dt>최근 검사</dt><dd>{formatDateTime(current?.last_checked_at)}</dd></div><div><dt>최근 교체</dt><dd>{formatDateTime(current?.updated_at)}</dd></div></dl></section>
    </div>
  </>;
}

export function SettingsPage({ theme, setTheme }: { theme: "light" | "dark"; setTheme: (value: "light" | "dark") => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [notificationState, setNotificationState] = useState(() => pageNotificationPermission());
  async function changePassword(event: FormEvent) {
    event.preventDefault(); setMessage("");
    try { await api.changePassword(currentPassword, newPassword); setCurrentPassword(""); setNewPassword(""); setMessage("비밀번호를 변경했고 다른 세션의 정책은 서버 설정에 따라 적용됩니다."); }
    catch (reason) { setMessage(reason instanceof ApiError ? reason.message : "비밀번호를 변경하지 못했습니다."); }
  }
  async function enableNotifications() {
    setNotificationState("default");
    try { setNotificationState(await requestPageNotificationPermission()); }
    catch { setNotificationState("unsupported"); }
  }
  const notificationLabel = notificationState === "granted" ? "허용됨 (페이지 열린 동안만)" : notificationState === "denied" ? "차단됨 — Chrome 사이트 설정에서 변경" : notificationState === "unsupported" ? "이 브라우저에서 지원하지 않음" : "권한 요청 전";
  return <><PageHeader eyebrow="SETTINGS" title="공통 설정" description="표시 환경, 로그인 보안과 페이지 열린 상태 알림을 관리합니다." /><div className="settings-grid"><section className="card"><h2>화면 테마</h2><p className="muted">테마는 금융정보가 아닌 기기 표시 설정으로만 브라우저에 저장됩니다.</p><div className="segmented" role="group" aria-label="화면 테마"><button className={theme === "light" ? "active" : ""} onClick={() => setTheme("light")}>라이트</button><button className={theme === "dark" ? "active" : ""} onClick={() => setTheme("dark")}>다크</button></div></section><section className="card"><h2>브라우저 열린 상태 알림</h2><p>이 버튼을 누를 때만 Chrome 알림 권한을 요청합니다. Service Worker·PWA·외부 Web Push는 사용하지 않습니다.</p><div className="button-row"><button className="secondary" onClick={enableNotifications} disabled={notificationState === "granted" || notificationState === "denied"}>페이지 알림 허용</button></div><p className="message neutral" role="status">{notificationLabel}</p><p className="muted">Chrome이나 이 페이지를 닫으면 브라우저 알림을 받을 수 없습니다. 상세 내용은 로그인 뒤 웹 내부 알림함에서 확인합니다.</p></section><form className="card stack-lg" onSubmit={changePassword}><h2>비밀번호 변경</h2><label>현재 비밀번호<input type="password" autoComplete="current-password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required /></label><label>새 비밀번호<input type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required minLength={12} /></label><button className="primary">비밀번호 변경</button>{message && <p className="message neutral" role="status">{message}</p>}</form><ManualAssetsPanel /></div><RuntimeBoundaryNotice /></>;
}

export function MarketPage() {
  const [queryDraft, setQueryDraft] = useState("");
  const [filters, setFilters] = useState<MarketFilters>(DEFAULT_MARKET_FILTERS);
  const [items, setItems] = useState<MarketInformationItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [status, setStatus] = useState<MarketInformationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [secContact, setSecContact] = useState("");
  const [secContactMessage, setSecContactMessage] = useState("");
  const [secContactBusy, setSecContactBusy] = useState(false);
  const credentialOverview = useLoad(api.credentials, []);
  const [retention, setRetention] = useState<MarketInformationRetentionResponse | null>(null);
  const [retentionLoading, setRetentionLoading] = useState(true);
  const [retentionLoadingMore, setRetentionLoadingMore] = useState(false);
  const [retentionError, setRetentionError] = useState("");
  const requestGeneration = useRef(0);
  const baselineReady = useRef(false);
  const knownHeadIds = useRef(new Set<string>());
  const visibleIds = useRef(new Set<string>());
  const pendingItems = useRef(new Map<string, MarketInformationItem>());

  useEffect(() => { visibleIds.current = new Set(items.map((item) => item.info_id)); }, [items]);

  const loadStatus = useCallback(async () => {
    try { setStatus(await api.marketInformationStatus()); setStatusError(""); }
    catch { setStatusError("공급자 상태를 확인하지 못했습니다."); }
  }, []);

  const loadRetention = useCallback(async () => {
    setRetentionLoading(true);
    setRetentionError("");
    try {
      setRetention(await api.marketInformationRetention({ status: "EXPIRED", limit: 25 }));
    } catch {
      setRetentionError("시장정보 보존 상태를 확인하지 못했습니다.");
    } finally {
      setRetentionLoading(false);
    }
  }, []);

  const loadVisible = useCallback(async (preserveItems = false) => {
    const generation = ++requestGeneration.current;
    baselineReady.current = false;
    setLoading(true);
    setError("");
    if (!preserveItems) {
      knownHeadIds.current = new Set();
      pendingItems.current.clear();
      setPendingCount(0);
      setItems([]);
      setNextCursor(null);
      setSnapshot("");
      setHasMore(false);
    }
    try {
      const visible = await api.marketInformation(marketQuery(filters, 25));
      if (generation !== requestGeneration.current) return;
      setItems(uniqueMarketItems(visible.items));
      setNextCursor(visible.next_cursor);
      setSnapshot(visible.snapshot);
      setHasMore(visible.has_more);
      pendingItems.current.clear();
      setPendingCount(0);
      try {
        const head = await api.marketInformation(marketQuery(filters, 100));
        if (generation !== requestGeneration.current) return;
        knownHeadIds.current = new Set(head.items.map((item) => item.info_id));
        baselineReady.current = true;
      } catch {
        setStatusError("새 정보 확인 기준을 준비하지 못했습니다. 현재 목록은 유지됩니다.");
      }
    } catch {
      if (generation === requestGeneration.current) setError("시장정보 목록을 불러오지 못했습니다.");
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [filters]);

  const checkForNew = useCallback(async () => {
    if (!baselineReady.current) return;
    try {
      const head = await api.marketInformation(marketQuery(filters, 100));
      const nextHeadIds = new Set(head.items.map((item) => item.info_id));
      for (const item of head.items) {
        if (!knownHeadIds.current.has(item.info_id) && !visibleIds.current.has(item.info_id)) {
          pendingItems.current.set(item.info_id, item);
        }
      }
      knownHeadIds.current = nextHeadIds;
      setPendingCount(pendingItems.current.size);
      setStatusError("");
    } catch {
      setStatusError("새 정보 여부를 확인하지 못했습니다. 읽던 목록은 유지됩니다.");
    }
  }, [filters]);

  useEffect(() => { void loadVisible(); void loadStatus(); void loadRetention(); }, [loadRetention, loadStatus, loadVisible]);

  useEffect(() => {
    const timer = window.setInterval(() => { void loadStatus(); void checkForNew(); }, 30_000);
    const onVisibility = () => { if (document.visibilityState === "visible") { void loadStatus(); void checkForNew(); } };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, [checkForNew, loadStatus]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setFilters((current) => ({ ...current, q: queryDraft.trim() }));
  }

  async function loadMore() {
    if (!nextCursor || !snapshot || loadingMore) return;
    setLoadingMore(true); setError("");
    try {
      const result = await api.marketInformation(marketQuery(filters, 25, nextCursor, snapshot));
      setItems((current) => uniqueMarketItems([...current, ...result.items]));
      setNextCursor(result.next_cursor);
      setHasMore(result.has_more);
    } catch { setError("다음 시장정보를 불러오지 못했습니다. 기존 목록은 유지됩니다."); }
    finally { setLoadingMore(false); }
  }

  async function loadMoreRetention() {
    if (!retention?.next_cursor || !retention.snapshot || retentionLoadingMore) return;
    setRetentionLoadingMore(true); setRetentionError("");
    try {
      const next = await api.marketInformationRetention({
        status: "EXPIRED",
        cursor: retention.next_cursor,
        snapshot: retention.snapshot,
        limit: 25,
      });
      setRetention((current) => current ? {
        ...next,
        summary: current.summary,
        items: uniqueRetentionItems([...current.items, ...next.items]),
      } : next);
    } catch {
      setRetentionError("다음 삭제 대상 미리보기를 불러오지 못했습니다. 기존 결과는 유지됩니다.");
    } finally {
      setRetentionLoadingMore(false);
    }
  }

  async function requestRefresh() {
    setRefreshing(true); setActionMessage("");
    try {
      const result = await api.refreshMarketInformation();
      setActionMessage(refreshResultMessage(result.status, result.new_items));
      await Promise.all([loadStatus(), checkForNew(), loadRetention()]);
    } catch (reason) {
      setActionMessage(reason instanceof ApiError ? reason.message : "수동 새로고침을 시작하지 못했습니다.");
    } finally { setRefreshing(false); }
  }

  async function saveSecContact(event: FormEvent) {
    event.preventDefault(); setSecContactBusy(true); setSecContactMessage("");
    try {
      await api.saveCredential("sec", "default", { user_agent: secContact });
      setSecContactMessage("SEC 요청 식별 연락처를 OS 보안 저장소에 등록했습니다. 원문은 다시 표시하지 않습니다.");
      setSecContact("");
      await loadStatus();
    } catch (reason) {
      setSecContactMessage(reason instanceof ApiError ? reason.message : "SEC 요청 식별 연락처를 저장하지 못했습니다.");
    } finally { setSecContact(""); setSecContactBusy(false); }
  }

  async function deleteSecContact() {
    setSecContactBusy(true); setSecContactMessage("");
    try {
      await api.deleteCredential("sec", "default");
      setSecContactMessage("SEC 요청 식별 연락처를 OS 보안 저장소에서 삭제했습니다.");
      await loadStatus();
    } catch { setSecContactMessage("SEC 요청 식별 연락처를 삭제하지 못했습니다."); }
    finally { setSecContact(""); setSecContactBusy(false); }
  }

  const providers = status?.providers ?? [];
  const institutionCount = new Set(providers.map((provider) => provider.institution_id || provider.provider_id)).size;
  const delayed = providers.filter((provider) => provider.stale || marketStatusIncludes(provider.status, "STALE", "DELAY", "RETRY"));
  const providerUnset = providers.length > 0 && providers.every((provider) => marketStatusIncludes(provider.status, "NOT_CONFIGURED", "NEEDS_CONFIGURATION", "UNAVAILABLE", "DISABLED"));
  const filtered = Boolean(filters.q || filters.category !== "ALL" || filters.related !== "ALL" || filters.provider);
  const credentialStorageUnavailable = storageUnavailable(credentialOverview.data);
  const retryBlocked = status?.retry_allowed === false;

  return <>
    <PageHeader eyebrow="시장 분석 · 시장정보 목록" title="미국 시장정보" description="미국 시장·기업·경제·공시 관련 정보를 확인하고 원문을 열람할 수 있습니다."
      actions={<button className="primary" onClick={requestRefresh} disabled={refreshing || status?.refreshing || retryBlocked}>{refreshing || status?.refreshing ? "갱신 중…" : retryBlocked ? "재시도 대기" : "수동 새로고침"}</button>} />
    <section className="metric-grid market-status-grid" aria-label="시장정보 수집 상태">
      <article className="metric"><span>마지막 시도</span><strong className="metric-time">{formatDateTime(status?.last_attempt_at)}</strong><small>작업 실행 상태이며 현재 자료 시각과 다릅니다.</small></article>
      <article className="metric"><span>마지막 수집 성공</span><strong className="metric-time">{formatDateTime(status?.last_success_at)}</strong><small>실패한 시도를 성공 시각으로 바꾸지 않습니다.</small></article>
      <article className="metric"><span>이번 수집 결과</span><strong>{collectionResultLabel(status?.current_result ?? status?.overall_state)}</strong><small>작업 결과와 현재 표시 자료 상태를 분리합니다.</small></article>
      <article className="metric"><span>현재 자료 기준시각</span><strong className="metric-time">{formatDateTime(status?.current_data_as_of)}</strong><small>{status?.using_previous_data ? "이번 수집 실패·시간 초과로 이전 정상 자료 사용" : "현재 표시 자료의 최근 성공 기준"}</small></article>
      <article className="metric"><span>자동 갱신</span><strong className="metric-time">{automaticStateLabel(status)}</strong><small>브라우저 목록 조회와 외부 공급자 수집 주기는 분리됩니다.</small></article>
      <article className="metric"><span>공급자 상태</span><strong>{providers.length ? `${institutionCount}개 기관` : "확인 필요"}</strong><small>{providers.length ? `${providers.length}개 공식 출처 · ` : ""}{status?.partial ? "일부 지연 또는 실패" : delayed.length ? `${delayed.length}개 출처 지연` : "표시된 최근 상태 기준"}</small></article>
      <article className="metric"><span>재시도</span><strong>{status?.retry_allowed === true ? "가능" : status?.retry_allowed === false ? "대기 또는 설정 필요" : "확인 필요"}</strong><small>{status?.next_retry_at ? `가능 예정 ${formatDateTime(status.next_retry_at)}` : "백엔드 허용 여부를 따릅니다."}</small></article>
      <article className="metric"><span>표시 목록</span><strong>{items.length ? `${items.length}건` : "자료 없음"}</strong><small>발행시각 미확인 자료는 별도로 표시됩니다.</small></article>
    </section>
    {status?.timeout_limitations && <p className="message neutral">전체 수집 상한 {status.collection_timeout_seconds ?? "확인 필요"}초 · {status.timeout_limitations}</p>}
    {(status?.timed_out_provider_ids?.length ?? 0) > 0 && <p className="message warning" role="alert">시간 초과 공급자: {status?.timed_out_provider_ids?.join(", ")}. 시간 초과를 정상 완료로 표시하지 않습니다.</p>}
    {(status?.incomplete_provider_ids?.length ?? 0) > 0 && <p className="message warning" role="alert">제한시간 내 미시작 공급자: {status?.incomplete_provider_ids?.join(", ")}. 요청을 시작한 시간 초과와 구분합니다.</p>}
    {(status?.failed_provider_ids?.length ?? 0) > 0 && <p className="message warning" role="alert">실패 공급자: {status?.failed_provider_ids?.join(", ")}. 성공한 자료와 이전 정상 자료는 유지됩니다.</p>}
    {status?.using_previous_data && <p className="message warning" role="status">이번 수집에 실패하거나 시간 초과된 출처는 이전 정상 자료를 사용 중입니다. 보존 만료 상태와 공급자 갱신 지연은 서로 다른 상태입니다.</p>}
    {(status?.partial || delayed.length > 0) && <p className="message warning" role="status">일부 공급자는 설정이 필요하거나 지연·재시도 대기 중입니다. 기존 정상 자료는 유지됩니다.</p>}
    {statusError && <p className="message warning" role="alert">{statusError}</p>}
    {actionMessage && <p className="message neutral" role="status">{actionMessage}</p>}
    <MarketProviderStatuses providers={providers} />
    <details className="card provider-contact-settings"><summary>SEC 자동조회 설정</summary><form className="stack-lg" onSubmit={saveSecContact} autoComplete="off">{credentialStorageUnavailable && <p className="message error" role="alert"><strong>보안 저장소 사용 불가</strong><br />백엔드가 사용 불가를 확인했습니다. 확인되지 않은 원인을 단정하거나 평문 저장으로 대체하지 않습니다.</p>}<p className="muted">SEC 공정접근 정책에 따라 영문 조직명과 연락 가능한 이메일을 한 칸에 입력합니다. 예: <code>My Organization contact@example.com</code>. 이 값은 OS 보안 저장소에만 보관하며 화면·API 응답·로그에 다시 표시하지 않습니다.</p><label>SEC User-Agent 연락정보<input type="password" autoComplete="off" spellCheck={false} value={secContact} onChange={(event) => setSecContact(event.target.value)} minLength={8} maxLength={256} required disabled={credentialStorageUnavailable} /></label><div className="button-row"><button className="primary" disabled={secContactBusy || credentialStorageUnavailable}>등록 · 교체</button><button className="danger-button" type="button" disabled={secContactBusy || credentialStorageUnavailable} onClick={deleteSecContact}>등록 삭제</button></div>{secContactMessage && <p className="message neutral" role="status">{secContactMessage}</p>}</form></details>
    <form
      className="card market-filters"
      role="search"
      aria-label="시장정보 검색·필터"
      onSubmit={submitSearch}
    >
      <div className="market-search-line"><label htmlFor="market-search">시장정보 검색<input id="market-search" value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} maxLength={100} aria-describedby="market-search-help" placeholder="원문 제목·출처·관련 종목" /></label><button className="secondary" type="submit">검색</button></div>
      <p id="market-search-help" className="muted">원문 제목·출처·검증된 관련 종목만 검색합니다. 기사 본문은 저장하거나 검색하지 않습니다.</p>
      <fieldset><legend>분류</legend><div className="segmented market-categories">{MARKET_CATEGORIES.map(([value, label]) => <button type="button" key={value} className={filters.category === value ? "active" : ""} aria-pressed={filters.category === value} onClick={() => setFilters((current) => ({ ...current, category: value }))}>{label}</button>)}</div></fieldset>
      <div className="market-selectors">
        <label>관련 종목<select value={filters.related} onChange={(event) => setFilters((current) => ({ ...current, related: event.target.value as MarketInformationRelatedFilter }))}><option value="ALL">전체</option><option value="RELATED">관련 종목 있음</option><option value="UNCONFIRMED">관련 종목 미확정</option></select></label>
        <label>출처<select value={filters.provider} onChange={(event) => setFilters((current) => ({ ...current, provider: event.target.value }))}><option value="">전체 출처</option>{providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.source_name || provider.provider_name || provider.provider_id}</option>)}</select></label>
        <label>정렬<select value={filters.sort} onChange={(event) => setFilters((current) => ({ ...current, sort: event.target.value as MarketInformationSort }))}><option value="PUBLISHED_DESC">최신 발행순</option><option value="COLLECTED_DESC">최근 수집순</option></select></label>
      </div>
    </form>
    {pendingCount > 0 && <section className="new-information-banner" role="status" aria-live="polite"><span><strong>새 정보 {pendingCount}건</strong> · 현재 검색·필터에 맞는 새 항목이 있습니다.</span><button className="secondary" onClick={() => void loadVisible(true)}>새 정보 보기</button></section>}
    {loading && <div className="message neutral" role="status">시장정보를 불러오는 중입니다…</div>}
    {error && <div className="message warning" role="alert">{error} {items.length ? "기존 자료 있음·갱신 실패" : "전체 실패"}</div>}
    {!loading && !error && !items.length && <section className="card empty market-empty"><h2>{providerUnset ? "공급자 미설정" : filtered ? "검색 결과 없음" : "실제 자료 없음"}</h2><p>{providerUnset ? "사용 가능한 공급자의 설정 또는 연결 상태를 확인해 주세요." : filtered ? "검색어나 필터를 바꿔 다시 확인해 주세요." : "현재 저장된 공식 시장정보가 없습니다. 미확인 값을 만들어 표시하지 않습니다."}</p></section>}
    {items.length > 0 && <section className="card market-results" aria-labelledby="market-results-title" aria-busy={loadingMore}><div className="card-title"><h2 id="market-results-title">시장정보 목록</h2><span>{filters.sort === "PUBLISHED_DESC" ? "최신 발행순" : "최근 수집순"}</span></div><ol className="market-list">{items.map((item) => <MarketInformationRow item={item} key={item.info_id} />)}</ol>{hasMore && <div className="market-more"><button className="secondary" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "불러오는 중…" : "더 보기"}</button></div>}</section>}
    <RetentionPreviewPanel data={retention} loading={retentionLoading} loadingMore={retentionLoadingMore} error={retentionError} onReload={loadRetention} onLoadMore={loadMoreRetention} />
    <p className="message neutral">원문 링크는 구조 안전성만 확인합니다. 현재 목적지의 접속 성공이나 기사 본문 내용을 확인했다는 뜻이 아닙니다.</p>
  </>;
}

interface MarketFilters {
  q: string;
  category: MarketInformationCategory;
  related: MarketInformationRelatedFilter;
  provider: string;
  sort: MarketInformationSort;
}

const DEFAULT_MARKET_FILTERS: MarketFilters = { q: "", category: "ALL", related: "ALL", provider: "", sort: "PUBLISHED_DESC" };
const MARKET_CATEGORIES: Array<[MarketInformationCategory, string]> = [["ALL", "전체"], ["MARKET", "시장"], ["COMPANY", "기업"], ["ECONOMY", "경제"], ["FILING", "공시"]];
const MARKET_CATEGORY_LABELS: Record<string, string> = { MARKET: "시장", COMPANY: "기업", ECONOMY: "경제", FILING: "공시" };

function marketQuery(filters: MarketFilters, limit: number, cursor?: string, snapshot?: string): MarketInformationQuery {
  return { ...filters, limit, cursor, snapshot };
}

function uniqueMarketItems(items: MarketInformationItem[]): MarketInformationItem[] {
  const seen = new Set<string>();
  const unique: MarketInformationItem[] = [];
  for (const item of items) {
    if (!item.info_id || seen.has(item.info_id)) continue;
    seen.add(item.info_id);
    unique.push(item);
  }
  return unique;
}

function uniqueRetentionItems(items: MarketInformationRetentionItem[]): MarketInformationRetentionItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (!item.info_id || seen.has(item.info_id)) return false;
    seen.add(item.info_id);
    return true;
  });
}

function marketStatusIncludes(value: string | null | undefined, ...parts: string[]): boolean {
  const normalized = value?.toUpperCase() ?? "";
  return parts.some((part) => normalized.includes(part));
}

function automaticStateLabel(status: MarketInformationStatus | null): string {
  if (status?.refreshing || status?.automatic_state === "RUNNING") return "실행 중";
  if (status?.automatic_state === "ENABLED_WHILE_BACKEND_RUNNING") return "활성(백엔드 실행 중 자동)";
  if (status?.automatic_state === "STOPPED") return "중지";
  return "확인 필요";
}

function collectionResultLabel(value: string | null | undefined): string {
  switch (value) {
    case "OK": return "전체 정상";
    case "NOT_MODIFIED": return "변경 없음";
    case "PARTIAL": return "부분 완료(일부 실패)";
    case "FAILED": return "전체 실패";
    case "TIMED_OUT": return "시간 초과";
    case "WAITING": return "대기";
    case "NEEDS_CONFIGURATION": return "설정 필요";
    case "NO_SOURCES": return "지원 출처 없음";
    case "ALREADY_RUNNING": return "이미 실행 중";
    case "NOT_STARTED_TIME_LIMIT": return "제한시간 내 미시작";
    case "NOT_RUN_IN_PROCESS": return "이번 프로세스 미실행";
    default: return value || "확인 필요";
  }
}

function retentionStatusLabel(value: string | null | undefined): string {
  switch (value) {
    case "WITHIN_RETENTION": return "보존 기간 내";
    case "EXPIRING_SOON": return "만료 예정";
    case "EXPIRED": return "보존 기간 만료";
    case "CALCULATION_UNAVAILABLE": return "계산 불가";
    default: return "계산 불가";
  }
}

function refreshResultMessage(status: string, count: number): string {
  if (marketStatusIncludes(status, "RUNNING", "STARTED", "ACCEPTED")) return "시장정보 수집을 시작했습니다. 읽던 목록은 자동으로 재정렬하지 않습니다.";
  if (status === "TIMED_OUT") return "시장정보 수집이 시간 초과로 끝났습니다. 성공한 자료와 이전 정상 자료는 유지됩니다.";
  if (status === "PARTIAL") return "시장정보 수집이 부분 완료되었습니다. 실패·시간 초과 공급자를 확인해 주세요.";
  if (status === "FAILED") return "시장정보 수집이 실패했습니다. 이전 정상 자료는 유지됩니다.";
  if (marketStatusIncludes(status, "OK", "COMPLETED", "SUCCESS", "DONE")) return count > 0 ? `수집을 마쳤습니다. 현재 조건에 맞는 새 정보는 별도로 확인합니다.` : "수집을 마쳤으며 새 항목이 없습니다.";
  if (marketStatusIncludes(status, "RETRY", "RATE")) return "호출 제한에 따라 재시도 대기 중입니다.";
  return "수동 새로고침 상태를 확인했습니다.";
}

function formatPublishedAt(value: string | null | undefined, precision: string | null | undefined): string {
  if (!value) return "발행시각 미확인";
  if (precision?.toUpperCase() === "DATE") {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    return match ? `${match[1]}. ${Number(match[2])}. ${Number(match[3])}. (날짜 정밀도)` : "발행일 확인 필요";
  }
  return formatDateTime(value);
}

function MarketProviderStatuses({ providers }: { providers: MarketInformationProviderStatus[] }) {
  if (!providers.length) return null;
  return <details className="card provider-status-details"><summary>공급자별 상태 {providers.length}개</summary><ul className="status-list">{providers.map((provider) => {
    const result = provider.this_collection_result || provider.current_result || provider.status;
    const completedNormally = result === "OK" || result === "NOT_MODIFIED";
    const scheduleAt = provider.next_retry_at || provider.next_allowed_at;
    const lastSuccess = provider.success_recorded === false ? "최근 성공 기록 없음" : `최근 성공 ${formatDateTime(provider.last_success_at)}`;
    const displayedErrorCode = provider.this_collection_error_code || provider.error_code;
    return <li key={provider.provider_id} className="provider-status-item" data-provider-id={provider.provider_id}><span><strong>{provider.source_name || provider.provider_name || provider.provider_id}</strong><small>{provider.source_name && provider.provider_name ? `${provider.provider_name} · ` : ""}이번 수집 {collectionResultLabel(result)}</small><small>마지막 시도 {formatDateTime(provider.last_attempt_at)} · {lastSuccess}</small><small>현재 자료 기준 {formatDateTime(provider.current_data_as_of)}{provider.using_previous_data ? " · 이전 정상 자료 사용" : ""}</small>{displayedErrorCode && <small>상태 코드 {displayedErrorCode}</small>}{scheduleAt && <small>{completedNormally ? "다음 확인" : "재시도"} {formatDateTime(scheduleAt)}{provider.retry_allowed === false ? " · 현재 대기" : provider.retry_allowed === true ? " · 재시도 가능" : ""}</small>}</span><StatusBadge value={provider.status} /></li>;
  })}</ul></details>;
}

function MarketInformationRow({ item }: { item: MarketInformationItem }) {
  const symbols = Array.isArray(item.related_symbols) ? item.related_symbols : [];
  const sources = Array.isArray(item.sources) ? item.sources : [];
  const held = item.holding_status?.toUpperCase() === "HELD";
  return <li><article className="market-row" data-info-id={item.info_id}>
    <div className="market-row-main"><div className="market-row-flags"><span className="badge warning">{MARKET_CATEGORY_LABELS[item.category] || "분류 확인 필요"}</span>{item.is_sample && <span className="badge warning">SAMPLE</span>}{held && <span className="badge holding">보유</span>}</div><h3 className="market-title" title={item.title_original}>{item.title_original || "원문 제목 없음"}</h3><p className="market-source">{item.source_name || item.provider_name || "출처 확인 필요"} · {item.language || "언어 미확인"}</p></div>
    <div className="market-row-time"><span>발행</span><time dateTime={item.published_at || undefined}>{formatPublishedAt(item.published_at, item.published_precision)}</time><small>수집 {formatDateTime(item.collected_at)}</small></div>
    <div className="market-symbols" aria-label="관련 종목">{symbols.length ? symbols.map((symbol) => <span key={symbol} className={held && symbols.length === 1 ? "held-symbol" : ""}>{symbol}</span>) : <span className="muted">관련 종목 미확정</span>}</div>
    <div className="market-link"><ExternalSourceLink url={item.original_url} status={item.link_status} label="원문 보기 ↗" /></div>
    <details className="market-metadata"><summary>수집시각·보존 및 식별정보</summary><dl className="facts"><div><dt>수집시각</dt><dd>{formatDateTime(item.collected_at)}</dd></div><div><dt>최초 수집시각</dt><dd>{formatDateTime(item.first_collected_at)}{item.first_collected_estimated ? " (추정)" : ""}</dd></div><div><dt>보존 기준</dt><dd>최초 수집시각부터 달력 기준 1년</dd></div><div><dt>만료 예정시각</dt><dd>{formatDateTime(item.retention_expires_at)}</dd></div><div><dt>보존 상태</dt><dd>{retentionStatusLabel(item.retention_status)}</dd></div><div><dt>공급자 항목 ID</dt><dd>{item.provider_item_id || "없음"}</dd></div><div><dt>원문 문서 ID</dt><dd>{item.source_document_id || "없음"}</dd></div></dl>{sources.length > 1 && <MarketSourceList sources={sources} />}</details>
  </article></li>;
}

function RetentionPreviewPanel({ data, loading, loadingMore, error, onReload, onLoadMore }: {
  data: MarketInformationRetentionResponse | null;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  onReload: () => Promise<void>;
  onLoadMore: () => Promise<void>;
}) {
  const summary = data?.summary;
  const expiredItems = (data?.items ?? []).filter((item) => item.retention_status === "EXPIRED");
  return <section className="card market-retention" aria-labelledby="market-retention-title" aria-busy={loading || loadingMore}>
    <div className="card-title"><div><h2 id="market-retention-title">보존 기간 · 삭제 대상 미리보기</h2><p className="muted">시장정보 메타데이터만 표시하며 공급자 갱신 지연과 보존 만료는 서로 다른 상태입니다.</p></div><button className="secondary" type="button" onClick={() => void onReload()} disabled={loading}>{loading ? "확인 중…" : "보존 상태 갱신"}</button></div>
    <div className="retention-summary" aria-label="시장정보 보존 집계">
      <div><span>총 정보 항목</span><strong>{summary ? `${summary.total_information_count}건` : "확인 필요"}</strong><small>집계 단위: 정보 항목</small></div>
      <div><span>총 출처 기록</span><strong>{summary ? `${summary.total_source_record_count}건` : "확인 필요"}</strong><small>집계 단위: 출처 기록</small></div>
      <div><span>보존 기간 만료</span><strong>{summary ? `${summary.expired_count}건` : "확인 필요"}</strong><small>미리보기 대상</small></div>
      <div><span>만료 예정</span><strong>{summary ? `${summary.expiring_soon_count}건` : "확인 필요"}</strong><small>만료 전 30일 이내</small></div>
      <div><span>계산 불가</span><strong>{summary ? `${summary.uncomputable_count}건` : "확인 필요"}</strong><small>최초 시각 확인 필요</small></div>
      <div><span>마지막 상태 확인</span><strong className="metric-time">{formatDateTime(summary?.checked_at)}</strong><small>표시 기준이며 삭제 시각이 아님</small></div>
    </div>
    <p className="message warning retention-notice">현재는 삭제 대상 확인만 지원합니다. 실제 삭제 및 자동 정리 기능은 활성화되어 있지 않습니다.</p>
    <p className="muted">금융원장·거래·보고서·추천·검증 기록은 이 미리보기 대상이 아닙니다. 만료 예정 표시는 실제 삭제 권한이나 실행 예정이라는 뜻이 아닙니다.</p>
    {error && <p className="message warning" role="alert">{error}</p>}
    {!loading && !error && !expiredItems.length && <p className="empty">현재 페이지에 만료된 시장정보 메타데이터가 없습니다.</p>}
    {expiredItems.length > 0 && <div className="table-wrap"><table><thead><tr><th>정보 항목</th><th>출처</th><th>최초 수집시각</th><th>만료 예정시각</th><th>보존 상태</th></tr></thead><tbody>{expiredItems.map((item) => <tr key={item.info_id}><td>{item.title_original || item.info_id}</td><td>{item.source_name || item.provider_name || item.provider_id}</td><td>{formatDateTime(item.first_collected_at)}{item.first_collected_estimated ? " (추정)" : ""}</td><td>{formatDateTime(item.retention_expires_at)}</td><td>{retentionStatusLabel(item.retention_status)}</td></tr>)}</tbody></table></div>}
    {data?.has_more && <div className="market-more"><button className="secondary" type="button" onClick={() => void onLoadMore()} disabled={loadingMore}>{loadingMore ? "불러오는 중…" : "삭제 대상 더 보기"}</button></div>}
  </section>;
}

function MarketSourceList({ sources }: { sources: MarketInformationSource[] }) {
  return <div className="market-source-list"><h4>출처별 원문</h4><ul>{sources.map((source, index) => <li key={`${source.provider_id || "source"}-${source.provider_item_id || index}`}><span>{source.source_name || source.provider_name || "출처 확인 필요"}{source.title_original ? ` · ${source.title_original}` : ""}</span><ExternalSourceLink url={source.original_url} status={source.link_status} label="원문 보기 ↗" /></li>)}</ul></div>;
}

function ExternalSourceLink({ url, status, label }: { url?: string | null; status?: string | null; label: string }) {
  const href = safeExternalHref(url, status);
  return href ? <a className="text-link external-link" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${label.replace(" ↗", "")} (외부 사이트, 새 탭)`}>{label}</a> : <span className="link-unavailable">원문 링크 사용 불가</span>;
}

export function RiskPage() {
  const state = useLoad(api.dashboard, []);
  return <><PageHeader eyebrow="POLICY FIRST" title="위험 관리" description="코어·위성, 개별종목·ETF 한도와 금지상품 규칙은 전략 설정과 별도로 적용됩니다." /><LoadMessage loading={state.loading} error={state.error} /><section className="card"><h2>현재 정책 경고</h2>{state.data?.warnings?.length ? <ul className="alert-list">{state.data.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul> : <p className="empty">검사할 보유자료가 없거나 현재 표시할 경고가 없습니다. ‘위험 없음’ 판정이 아닙니다.</p>}<div className="rule-strip"><span>코어·위성 분리</span><span>보호저축 제외</span><span>금지상품 유지</span><span>주문 기능 없음</span></div></section></>;
}

export function SearchPage() { return <CapabilityPage title="종목 · ETF 검색" description="연결된 공급자 범위 안에서만 종목을 찾고 자료 기준시각을 함께 표시합니다." status="API 연결 필요" prerequisites={["지원 시장별 종목 마스터 공급자 확인", "검색 API와 기준시각 저장", "호출 제한·이용권한 검증"]} />; }
export function AnalysisPage() {
  const state = useLoad(api.portfolio, []);
  return <><PageHeader eyebrow="ANALYSIS INPUT" title="종목 · ETF 분석" description="현재 원장 식별자와 자료 상태를 표시합니다. 점수·전망은 검증된 시장자료가 있을 때만 생성합니다." /><LoadMessage loading={state.loading} error={state.error} />{state.data && <section className="card"><div className="card-title"><h2>분석 대상 원장</h2><span>{state.data.positions.length ? `${state.data.positions.length}건` : "미입력"}</span></div>{state.data.positions.length ? <div className="table-wrap"><table><thead><tr><th>종목</th><th>식별자</th><th>시장</th><th>자료 상태</th><th>기준시각</th></tr></thead><tbody>{state.data.positions.map((position) => <tr key={String(position.id)}><td>{position.name || "이름 미입력"}</td><td>{position.instrument_id}</td><td>{position.market || "확인 필요"}</td><td><StatusBadge value={position.status || "평가 불가"} /></td><td>{formatDateTime(position.as_of)}</td></tr>)}</tbody></table></div> : <p className="empty">분석 대상이 없습니다. 가상 종목이나 수익률을 만들지 않습니다.</p>}<p className="message warning">가격·거래량·재무·공시가 같은 cutoff로 확인되지 않으면 후보 평가는 불가입니다.</p></section>}</>;
}
export function AccountsPage() {
  const state = useLoad(api.portfolio, []);
  return <><PageHeader eyebrow="ACCOUNTS" title="계좌 · ISA 관리" description="검증된 수동 원장 계좌만 표시하며 계좌 조회와 주문은 지원하지 않습니다." /><LoadMessage loading={state.loading} error={state.error} />{state.data && <section className="card"><div className="card-title"><h2>계좌 원장</h2><span>{state.data.accounts.length ? `${state.data.accounts.length}건` : "미입력"}</span></div>{state.data.accounts.length ? <div className="table-wrap"><table><thead><tr><th>별칭</th><th>유형</th><th>세제 상태</th><th>자료 영역</th><th>평가액</th><th>기준시각</th><th>상태</th></tr></thead><tbody>{state.data.accounts.map((account) => <tr key={String(account.id)}><td>{account.alias || "별칭 미입력"}</td><td>{account.account_type || "확인 필요"}</td><td>{account.tax_treatment || "확인 필요"}</td><td>{displayScope(account.data_scope)}</td><td>{formatKrw(account.market_value_krw)}</td><td>{formatDateTime(account.as_of)}</td><td><StatusBadge value={account.status} /></td></tr>)}</tbody></table></div> : <p className="empty">계좌 원장이 없습니다. 초기자산 선언값을 계좌 잔고로 만들지 않습니다.</p>}<ul className="boundary-list account-boundaries"><li>시세 수집용 KIS 인증정보를 계좌 조회나 주문에 재사용하지 않음</li><li>ISA 세제 수치는 공식 적용일 확인 전 계산하지 않음</li><li>보호저축·비상금은 투자자산에서 분리</li></ul></section>}</>;
}
export function TaxPage() { return <CapabilityPage title="세금 비교 · 매도 시뮬레이션" description="상품·계좌·적용일별 공식 세금 규칙이 검증되기 전에는 세후 금액을 확정하지 않습니다." status="미구현" prerequisites={["공식 세법 근거와 기준일", "계좌·상품 과세 유형 입력", "비용·결제 시점 모델"]} />; }
export function DividendPage() { return <CapabilityPage title="배당 현금흐름" description="실제 수령, 예상 배당, 배당 전환 능력을 구분해 표시할 예정입니다." status="자료 미연결" prerequisites={["배당 지급 이력", "세전·세후 구분", "통화·환율 기준시각"]} />; }
export function PerformancePage() { return <CapabilityPage title="과거 추천 · 투자 성과 검증" description="당시 자료·모델 버전·근거를 보존한 기록만 평가합니다." status="기록 없음" prerequisites={["불변 추천 버전 저장", "배당·비용·비교지수 기준", "미래정보·생존편향 방지 검증"]} />; }

export function NotFoundPage() { return <CapabilityPage title="화면을 찾을 수 없습니다" description="주소를 확인하거나 왼쪽 메뉴에서 이동해 주세요." prerequisites={["지원 화면 선택"]} />; }

export type { CredentialStatus, DashboardData };
