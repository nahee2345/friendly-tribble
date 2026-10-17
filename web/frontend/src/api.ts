import type {
  CredentialOverview,
  CredentialStatus,
  DashboardData,
  JobSummary,
  LocalNotificationFeed,
  MarketInformationListResponse,
  MarketInformationQuery,
  MarketInformationRefreshResult,
  MarketInformationRetentionFilter,
  MarketInformationRetentionResponse,
  MarketInformationStatus,
  PortfolioData,
  ReportDetail,
  ReportSummary,
  SessionResponse,
  StrategyCatalogItem,
  StrategyPreview,
  StrategyTargetVersion,
  StrategyWeights,
  SystemStatus
} from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number, message = "요청을 완료하지 못했습니다.") {
    super(message);
    this.name = "ApiError";
  }
}

function csrfToken(): string | undefined {
  const item = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("wealthy_csrf="));
  if (!item) return undefined;
  try { return decodeURIComponent(item.slice("wealthy_csrf=".length)); } catch { return undefined; }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const token = csrfToken();
    if (token) headers.set("X-CSRF-Token", token);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store"
  });
  if (!response.ok) {
    const safeMessages: Record<number, string> = {
      400: "입력값을 확인해 주세요.",
      401: "로그인이 필요합니다.",
      403: "이 작업을 수행할 권한이 없습니다.",
      404: "요청한 항목을 찾을 수 없습니다.",
      409: "같은 이름의 다른 버전이 이미 있습니다.",
      413: "파일이 허용된 크기를 초과했습니다.",
      429: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
    };
    throw new ApiError(response.status, safeMessages[response.status]);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  session: () => request<SessionResponse>("/api/v1/session"),
  login: (username: string, password: string) => request<{ user: SessionResponse["user"] }>("/api/v1/login", {
    method: "POST", body: JSON.stringify({ username, password })
  }),
  logout: () => request<void>("/api/v1/logout", { method: "POST" }),
  changePassword: (currentPassword: string, newPassword: string) => request<void>("/api/v1/password", {
    method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  }),
  status: () => request<SystemStatus>("/api/v1/status"),
  dashboard: () => request<DashboardData>("/api/v1/dashboard").then(normalizeDashboard),
  notifications: () => request<LocalNotificationFeed>("/api/v1/notifications"),
  portfolio: () => request<PortfolioData>("/api/v1/portfolio").then(normalizePortfolio),
  loadSample: () => request<{ dashboard: DashboardData }>("/api/v1/scenarios/sample", { method: "POST" }).then((result) => normalizeDashboard(result.dashboard)),
  uploadScenario: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return request<{ dashboard: DashboardData }>("/api/v1/scenarios/upload", { method: "POST", body }).then((result) => normalizeDashboard(result.dashboard));
  },
  reports: () => request<{ items: ReportSummary[] }>("/api/v1/reports").then((result) => result.items.map(normalizeReport)),
  report: (id: string | number) => request<ReportDetail>(`/api/v1/reports/${encodeURIComponent(id)}`).then(normalizeReportDetail),
  strategyCurrent: () => request<{ target: StrategyTargetVersion | null }>("/api/v1/strategy-targets/current").then((result) => result.target),
  strategyHistory: () => request<{ items: StrategyTargetVersion[] }>("/api/v1/strategy-targets/history").then((result) => result.items),
  strategyPreview: (weights: StrategyWeights) => request<StrategyPreview>("/api/v1/strategy-targets/preview", {
    method: "POST", body: JSON.stringify({ weights })
  }),
  saveStrategy: (version: string, weights: StrategyWeights) => request<StrategyTargetVersion>("/api/v1/strategy-targets", {
    method: "POST", body: JSON.stringify({ version, weights })
  }),
  strategyCatalog: () => request<{ items: Array<StrategyCatalogItem & { criteria?: string[] | string; benefits?: string[] | string; risks?: string[] | string; required_data?: StrategyCatalogItem["required_data"] | string }> }>("/api/v1/strategy-catalog").then((result) => ({ items: result.items.map(normalizeCatalogItem) })),
  credentials: () => request<CredentialOverview>("/api/v1/credentials"),
  saveCredential: (provider: string, environment: string, fields: Record<string, string>) => request<CredentialStatus>(
    `/api/v1/credentials/${encodeURIComponent(provider)}/${encodeURIComponent(environment)}`,
    { method: "PUT", body: JSON.stringify({ values: fields }) }
  ),
  deleteCredential: (provider: string, environment: string) => request<void>(
    `/api/v1/credentials/${encodeURIComponent(provider)}/${encodeURIComponent(environment)}`,
    { method: "DELETE" }
  ),
  jobs: () => request<{ items: Array<JobSummary & { state?: string }> }>("/api/v1/jobs").then((result) => result.items.map((job) => ({ ...job, status: job.status ?? job.state ?? "확인 필요" }))),
  marketInformation: (query: MarketInformationQuery) => request<MarketInformationListResponse>(marketInformationPath(query)),
  marketInformationStatus: () => request<MarketInformationStatus>("/api/v1/market-information/status"),
  refreshMarketInformation: () => request<MarketInformationRefreshResult>("/api/v1/market-information/refresh", { method: "POST" }),
  marketInformationRetention: (query: {
    status?: MarketInformationRetentionFilter;
    cursor?: string;
    snapshot?: string;
    limit?: number;
  } = {}) => request<MarketInformationRetentionResponse>(marketInformationRetentionPath(query))
};

function marketInformationPath(query: MarketInformationQuery): string {
  const params = new URLSearchParams();
  const q = query.q?.trim();
  if (q) params.set("q", q);
  params.set("category", query.category);
  params.set("related", query.related);
  if (query.provider) params.set("provider", query.provider);
  params.set("sort", query.sort);
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.limit != null) params.set("limit", String(query.limit));
  if (query.snapshot) params.set("snapshot", query.snapshot);
  return `/api/v1/market-information?${params.toString()}`;
}

function marketInformationRetentionPath(query: {
  status?: MarketInformationRetentionFilter;
  cursor?: string;
  snapshot?: string;
  limit?: number;
}): string {
  const params = new URLSearchParams();
  params.set("status", query.status ?? "EXPIRED");
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.snapshot) params.set("snapshot", query.snapshot);
  params.set("limit", String(query.limit ?? 25));
  return `/api/v1/market-information/retention?${params.toString()}`;
}

function normalizeDashboard(value: DashboardData): DashboardData {
  return { ...value, providers: value.providers ?? value.provider_statuses ?? [] };
}

function normalizePortfolio(value: Partial<PortfolioData>): PortfolioData {
  return {
    ...value,
    accounts: Array.isArray(value.accounts) ? value.accounts : [],
    positions: Array.isArray(value.positions) ? value.positions : [],
    asset_class_totals: Array.isArray(value.asset_class_totals) ? value.asset_class_totals : [],
    monthly_history: Array.isArray(value.monthly_history) ? value.monthly_history : []
  };
}

function normalizeReport(value: ReportSummary & { data_cutoff_at?: string | null }): ReportSummary {
  return { ...value, as_of: value.as_of ?? value.data_cutoff_at ?? null };
}

function normalizeReportDetail(value: ReportDetail & { data_cutoff_at?: string | null }): ReportDetail {
  return { ...value, as_of: value.as_of ?? value.data_cutoff_at ?? null };
}

function stringList(value: string[] | string | undefined): string[] {
  if (Array.isArray(value)) return value;
  return value ? [value] : [];
}

function normalizeCatalogItem(value: StrategyCatalogItem & { criteria?: string[] | string; benefits?: string[] | string; risks?: string[] | string; required_data?: StrategyCatalogItem["required_data"] | string }): StrategyCatalogItem {
  return {
    ...value,
    criteria: stringList(value.criteria),
    benefits: stringList(value.benefits),
    risks: stringList(value.risks),
    required_data: typeof value.required_data === "string"
      ? [{ name: value.required_data, status: "unavailable" }]
      : value.required_data ?? []
  };
}
