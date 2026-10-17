export const TARGET_STRATEGY_IDS = [
  "LONG_TERM_INDEX",
  "VALUE",
  "QUALITY_GROWTH",
  "GROWTH",
  "HIGH_DIVIDEND",
  "DIVIDEND_GROWTH",
  "MOMENTUM",
  "ASSET_ALLOCATION"
] as const;

export type TargetStrategyId = (typeof TARGET_STRATEGY_IDS)[number];
export type StrategyWeights = Record<TargetStrategyId, string>;

export interface UserSummary {
  id: string;
  username: string;
}

export interface SessionResponse {
  authenticated: boolean;
  user: UserSummary | null;
}

export interface SystemStatus {
  mode?: string;
  database?: string;
  scheduler?: string;
  timezone?: string;
  next_collection_at?: string | null;
  next_report_at?: string | null;
  registration_enabled?: boolean;
  orders_enabled?: boolean;
  providers?: Array<{ provider: string; status: string; as_of?: string | null }>;
}

export interface LocalNotificationItem {
  id: string | number;
  kind: string;
  title: string;
  message: string;
  created_at: string;
  status: string;
}

export interface LocalNotificationFeed {
  items: LocalNotificationItem[];
  browser_notifications: "PAGE_OPEN_ONLY";
}

export interface DashboardData {
  data_scope?: string | null;
  as_of?: string | null;
  collected_at?: string | null;
  investable_total_krw?: string | null;
  declared_initial_assets_krw?: string | null;
  warnings?: string[];
  stale?: boolean;
  source_status?: string;
  latest_report?: { id: string; created_at: string; status: string } | null;
  providers?: Array<{ provider: string; status: string; as_of?: string | null }>;
  provider_statuses?: Array<{ provider: string; status: string; as_of?: string | null }>;
}

export interface StrategyTargetVersion {
  id?: string;
  version: string;
  created_at?: string;
  weights: StrategyWeights;
  total_assets_krw?: string | null;
  target_amounts_krw?: Partial<Record<TargetStrategyId, string | null>>;
}

export interface PortfolioAccount {
  id: string | number;
  alias?: string | null;
  account_type?: string | null;
  tax_treatment?: string | null;
  market_value_krw?: string | null;
  data_scope?: string | null;
  as_of?: string | null;
  status?: string | null;
}

export interface PortfolioPosition {
  id: string | number;
  instrument_id: string | null;
  name?: string | null;
  instrument_type?: string | null;
  market?: string | null;
  currency?: string | null;
  quantity?: string | null;
  market_value_krw?: string | null;
  asset_class?: string | null;
  as_of?: string | null;
  status?: string | null;
}

export interface AssetClassTotal {
  asset_class: string;
  amount_krw?: string | null;
  weight_percent?: string | null;
  status?: string | null;
}

export interface MonthlyPortfolioPoint {
  month: string;
  investable_total_krw?: string | null;
  net_contributions_krw?: string | null;
  investment_pnl_krw?: string | null;
  data_scope?: string | null;
  as_of?: string | null;
  status?: string | null;
}

export interface ActualStrategyAllocation {
  status: string;
  as_of?: string | null;
  weights?: Partial<Record<TargetStrategyId, string>> | null;
  missing_reason?: string | null;
  double_count_checked?: boolean | null;
  transition_status?: string | null;
  transition_summary?: string | null;
  next_review_at?: string | null;
}

export interface PortfolioData {
  data_scope?: string | null;
  as_of?: string | null;
  stale?: boolean;
  investable_total_krw?: string | null;
  accounts: PortfolioAccount[];
  positions: PortfolioPosition[];
  asset_class_totals: AssetClassTotal[];
  monthly_history: MonthlyPortfolioPoint[];
  actual_strategy?: ActualStrategyAllocation | null;
}

export interface StrategyPreview {
  valid: boolean;
  total: string;
  delta?: string;
  direction?: "add" | "reduce" | "none" | "invalid";
  errors?: string[];
  total_assets_krw?: string | null;
  target_amounts_krw?: Partial<Record<TargetStrategyId, string | null>>;
}

export interface StrategyCatalogItem {
  id: TargetStrategyId;
  name: string;
  description?: string;
  philosophy?: string;
  criteria?: string[];
  benefits?: string[];
  risks?: string[];
  required_data?: Array<{ name: string; status: "connected" | "missing" | "unavailable"; as_of?: string | null }>;
  status?: string;
  mapping?: string;
}

export interface ReportSummary {
  id: string | number;
  created_at: string;
  status: string;
  data_scope?: string | null;
  generation_kind?: "MANUAL" | "SCHEDULED" | null;
  as_of?: string | null;
  title?: string;
}

export interface ReportDetail extends ReportSummary {
  markdown: string;
}

export interface CredentialStatus {
  provider: string;
  environment: string;
  configured: boolean;
  updated_at?: string | null;
  last_checked_at?: string | null;
  connection_status?: string | null;
}

export interface CredentialStorageStatus {
  available: boolean | null;
  confirmed?: boolean;
  state?: "AVAILABLE_CONFIRMED" | "UNAVAILABLE_CONFIRMED" | "UNKNOWN" | string;
  backend?: string | null;
  plaintext_fallback: boolean;
  checked_at?: string | null;
  safe_recheck_allowed?: boolean;
}

export interface CredentialOverview {
  items: CredentialStatus[];
  notice?: string;
  storage?: CredentialStorageStatus | null;
}

export interface JobSummary {
  id: string | number;
  kind: string;
  scheduled_for: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  message?: string | null;
}

export type MarketInformationCategory = "ALL" | "MARKET" | "COMPANY" | "ECONOMY" | "FILING";
export type MarketInformationRelatedFilter = "ALL" | "RELATED" | "UNCONFIRMED";
export type MarketInformationSort = "PUBLISHED_DESC" | "COLLECTED_DESC";

export interface MarketInformationSource {
  provider_id?: string | null;
  provider_name?: string | null;
  source_name?: string | null;
  title_original?: string | null;
  original_url?: string | null;
  link_status?: string | null;
  published_at?: string | null;
  published_precision?: string | null;
  provider_item_id?: string | null;
  source_document_id?: string | null;
}

export interface MarketInformationItem {
  info_id: string;
  provider_id: string;
  provider_name: string;
  source_name: string;
  title_original: string;
  language: string;
  category: Exclude<MarketInformationCategory, "ALL">;
  related_symbols: string[];
  holding_status: string;
  original_url: string | null;
  link_status: string;
  published_at: string | null;
  collected_at: string;
  provider_item_id: string | null;
  source_document_id: string | null;
  published_precision: string;
  is_sample: boolean;
  sources: MarketInformationSource[];
  first_collected_at?: string | null;
  retention_expires_at?: string | null;
  retention_status?: string | null;
  first_collected_estimated?: boolean | null;
}

export interface MarketInformationListResponse {
  items: MarketInformationItem[];
  next_cursor: string | null;
  snapshot: string;
  has_more: boolean;
}

export interface MarketInformationQuery {
  q?: string;
  category: MarketInformationCategory;
  related: MarketInformationRelatedFilter;
  provider?: string;
  sort: MarketInformationSort;
  cursor?: string;
  limit?: number;
  snapshot?: string;
}

export interface MarketInformationProviderStatus {
  provider_id: string;
  institution_id?: string | null;
  provider_name?: string | null;
  source_name?: string | null;
  status: string;
  this_collection_result?: string | null;
  this_collection_error_code?: string | null;
  current_result?: string | null;
  last_attempt_at?: string | null;
  last_success_at?: string | null;
  current_data_as_of?: string | null;
  next_retry_at?: string | null;
  next_allowed_at?: string | null;
  retry_allowed?: boolean | null;
  retry_wait?: boolean | null;
  retry_after_seconds?: number | null;
  error_code?: string | null;
  timed_out?: boolean | null;
  success_recorded?: boolean | null;
  using_previous_data?: boolean | null;
  stale?: boolean;
  message?: string | null;
}

export interface MarketInformationRefreshSummary {
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  failed_provider_ids?: string[];
  timed_out_provider_ids?: string[];
  incomplete_provider_ids?: string[];
  using_previous_data?: boolean;
}

export interface MarketInformationStatus {
  automatic_state: string;
  overall_state?: string;
  current_result?: string | null;
  last_attempt_at?: string | null;
  last_success_at: string | null;
  current_data_as_of?: string | null;
  next_retry_at?: string | null;
  retry_allowed?: boolean | null;
  failed_provider_ids?: string[];
  timed_out_provider_ids?: string[];
  incomplete_provider_ids?: string[];
  using_previous_data?: boolean | null;
  collection_timeout_seconds?: number | null;
  timeout_limitations?: string | null;
  runtime_notice?: string | null;
  last_refresh?: MarketInformationRefreshSummary | null;
  refreshing: boolean;
  partial: boolean;
  providers: MarketInformationProviderStatus[];
}

export interface MarketInformationRefreshResult {
  status: string;
  new_items: number;
  started_at: string | null;
  finished_at: string | null;
}

export type MarketInformationRetentionFilter = "EXPIRED";

export interface MarketInformationRetentionItem {
  info_id: string;
  provider_id: string;
  provider_name?: string | null;
  source_name?: string | null;
  title_original?: string | null;
  first_collected_at: string | null;
  retention_expires_at: string | null;
  retention_status: string;
  first_collected_estimated: boolean | null;
}

export interface MarketInformationRetentionSummary {
  total_information_count: number;
  total_source_record_count: number;
  expired_count: number;
  expiring_soon_count: number;
  uncomputable_count: number;
  within_retention_count?: number;
  checked_at: string | null;
  information_count_unit?: string;
  source_record_count_unit?: string;
}

export interface MarketInformationRetentionResponse {
  summary: MarketInformationRetentionSummary;
  items: MarketInformationRetentionItem[];
  next_cursor: string | null;
  snapshot: string;
  has_more: boolean;
  deletion_enabled?: boolean;
  notice?: string;
}
