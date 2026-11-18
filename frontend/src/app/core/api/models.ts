/**
 * Typed models for everything the backend API returns (see /api/docs).
 * These mirror `backend/app/api/schemas.py`; the API is the contract.
 */

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';
export type StepStatus = 'running' | 'ok' | 'degraded' | 'failed' | 'skipped';
export type StageStatus = 'pending' | StepStatus;
export type Intent = 'informational' | 'navigational' | 'commercial' | 'transactional';
export type ChannelName = 'organic' | 'ai_overview' | 'ai_answers';
export type MeasureStatus = 'ok' | 'failed' | 'not_measured';
export type QueryStatus = 'measured' | 'partial' | 'failed';
export type RecommendationStatus = 'open' | 'in_progress' | 'done' | 'dismissed';
export type GapType =
  | 'absent_everywhere'
  | 'missing_in_ai_answers'
  | 'missing_in_ai_overview'
  | 'ranking_low_organic'
  | 'competitor_dominated'
  | 'already_strong';

export interface Competitor {
  id: string;
  name: string;
  domain: string;
}

export interface RunBrief {
  id: string;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  visibility_score: number | null;
  question: string | null;
}

export interface TrendPoint {
  run_id: string;
  finished_at: string | null;
  visibility_score: number | null;
  status: RunStatus;
}

export interface Brand {
  id: string;
  name: string;
  domain: string;
  market: string;
  description: string;
  location_code: number;
  location_name: string;
  language_code: string;
  seed_topics: string[];
  competitors: Competitor[];
  created_at: string;
  updated_at: string;
  latest_run: RunBrief | null;
  latest_completed_run: RunBrief | null;
  active_run_id: string | null;
  runs_count: number;
  trend: TrendPoint[];
}

export interface BrandInput {
  name: string;
  domain: string;
  market: string;
  description: string;
  location_code: number;
  language_code: string;
  seed_topics: string[];
  competitors: { name: string; domain: string }[];
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export interface Page<T> extends ListResponse<T> {
  limit: number;
  offset: number;
}

export interface StageProgress {
  stage: string;
  label: string;
  status: StageStatus;
  detail: string | null;
  completed: number;
  total: number;
}

export interface Degradation {
  node: string;
  kind: 'fallback' | 'partial' | 'skipped' | 'failed' | 'budget';
  message: string;
}

export interface ChannelCoverage {
  measured: number;
  visible: number;
}

export interface ShareOfVoiceEntry {
  name: string;
  is_brand: boolean;
  appearances: number;
  surfaces: number;
  share: number;
}

export interface ReportSummary {
  visibility_score: number | null;
  queries_measured: number;
  queries_partial: number;
  queries_failed: number;
  gaps_worth_closing: number;
  channel_coverage: Record<ChannelName, ChannelCoverage>;
  share_of_voice: ShareOfVoiceEntry[];
  top_opportunity: { query: string; score: number } | null;
}

export interface Report {
  generated_at: string;
  brand: { name: string; domain: string; market: string; location: string };
  question: string | null;
  summary: ReportSummary;
  insights: string[];
  opportunities: {
    ref: string;
    query: string;
    score: number;
    visibility: number | null;
    summary: string;
    worth_closing: boolean | null;
    reason: string | null;
  }[];
  recommendations: { query: string; title: string; content_type: string; target_channels: string[]; effort: string }[];
  data_quality: {
    complete: boolean;
    plan_source: string | null;
    analysis_source: string | null;
    recommendations_source: string | null;
    degradations: Degradation[];
  };
  markdown: string;
}

export interface RunMetrics {
  nodes: Record<string, { executions: number; total_ms: number; max_ms: number; success_rate: number }>;
  api_calls_total: number;
  retries: number;
  cache_hits: number;
  cost_usd: number;
}

export interface Run {
  id: string;
  brand_id: string;
  status: RunStatus;
  question: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  current_node: string | null;
  retry_of_run_id: string | null;
  data_mode: 'live' | 'fixtures';
  llm_model: string;
  visibility_score: number | null;
  cost_usd: number;
  degradations: Degradation[];
  error: { code: string; message: string } | null;
  stages: StageProgress[];
  report: Report | null;
  metrics: RunMetrics | null;
  profile: { name: string; domain: string; competitors: { name: string; domain: string }[]; location_name: string };
}

export interface ScoreFactor {
  name: 'demand' | 'gap' | 'urgency' | 'winnability' | 'intent_value';
  value: number;
  explanation: string;
}

export interface ScoreBreakdown {
  score: number;
  visibility: number | null;
  factors: ScoreFactor[];
  channel_visibility: Record<ChannelName, number | null>;
  summary: string;
}

export interface SerpResult {
  position: number;
  domain: string;
  title: string;
  url: string;
}

export interface OrganicChannel {
  status: MeasureStatus;
  brand_position: number | null;
  brand_url: string | null;
  competitor_positions: Record<string, number>;
  top_results: SerpResult[];
  people_also_ask: string[];
}

export interface AIOverviewChannel {
  status: MeasureStatus;
  present: boolean;
  brand_cited: boolean;
  brand_mentioned: boolean;
  competitors_cited: string[];
  references: { domain: string; url: string; title: string }[];
}

export interface AIEngineChannel {
  engine: string;
  model: string;
  status: MeasureStatus;
  brand_mentioned: boolean;
  brand_cited: boolean;
  competitors_mentioned: string[];
  cited_domains: string[];
  excerpt: string;
  brand_context: string | null;
}

export interface GapJudgement {
  query_ref: string;
  worth_closing: boolean;
  gap_type: GapType;
  reason: string;
  competitors_winning: string[];
  confidence: 'low' | 'medium' | 'high';
  source: 'llm' | 'rule';
}

export interface QueryResult {
  id: string;
  run_id: string;
  rank: number;
  text: string;
  prompt: string;
  source: string;
  intent: Intent | null;
  search_volume: number | null;
  competition: number | null;
  cpc: number | null;
  keyword_difficulty: number | null;
  status: QueryStatus;
  visibility: number | null;
  opportunity_score: number;
  score_breakdown: ScoreBreakdown;
  channels: { organic: OrganicChannel; ai_overview: AIOverviewChannel; ai_engines: AIEngineChannel[] };
  analysis: GapJudgement | null;
  errors: { stage: string; code: string; message: string; tool: string | null }[];
  recommendation_ids: string[];
}

export interface QueryPage extends Page<QueryResult> {
  facets: Record<'intent' | 'missing_in' | 'status', Record<string, number>>;
}

export interface QueryHistoryPoint {
  run_id: string;
  measured_at: string | null;
  visibility: number | null;
  opportunity_score: number;
  organic_position: number | null;
  ai_mentions: number;
  ai_measured: number;
}

export interface QueryDetail extends QueryResult {
  history: QueryHistoryPoint[];
}

export interface QueryFilter {
  search?: string;
  intent?: Intent[];
  missing_in?: ChannelName;
  worth_closing?: boolean;
  min_volume?: number;
  sort?: '-score' | 'score' | '-volume' | 'volume' | 'visibility' | '-visibility';
  limit?: number;
  offset?: number;
}

export interface Recommendation {
  id: string;
  run_id: string;
  brand_id: string;
  query_id: string;
  query_text: string;
  priority: number;
  title: string;
  content_type: string;
  effort: 'low' | 'medium' | 'high';
  target_channels: ChannelName[];
  rationale: string;
  outline: string[];
  key_points: string[];
  sources_to_earn: string[];
  source: 'llm' | 'rule';
  status: RecommendationStatus;
  status_updated_at: string | null;
  created_at: string;
  opportunity_score: number;
}

export interface QueryDelta {
  text: string;
  change: 'improved' | 'declined' | 'unchanged' | 'new' | 'dropped';
  baseline_visibility: number | null;
  current_visibility: number | null;
  visibility_delta: number | null;
  baseline_score: number | null;
  current_score: number | null;
  baseline_position: number | null;
  current_position: number | null;
  baseline_ai_mentions: number | null;
  current_ai_mentions: number | null;
}

export interface RunComparison {
  current: RunBrief;
  baseline: RunBrief | null;
  visibility_delta: number | null;
  improved: number;
  declined: number;
  unchanged: number;
  new: number;
  dropped: number;
  queries: QueryDelta[];
  channel_coverage: Record<ChannelName, { current: ChannelCoverage | null; baseline: ChannelCoverage | null }>;
}

export interface BrandOverview {
  brand: Brand;
  open_recommendations: number;
  gaps_worth_closing: number | null;
  top_opportunity: { query: string; score: number } | null;
  visibility_delta: number | null;
}

export interface Overview {
  brands: BrandOverview[];
  totals: {
    brands: number;
    average_visibility: number | null;
    open_recommendations: number;
    active_runs: number;
    never_measured: number;
  };
}

export interface Location {
  code: number;
  name: string;
  iso: string;
  languages: { code: string; name: string }[];
}

export interface Meta {
  version: string;
  environment: string;
  data_mode: 'live' | 'fixtures';
  synthetic_data: boolean;
  llm_provider: string;
  llm_model: string;
  ai_engines: { engine: string; model: string }[];
  configured: boolean;
  configuration_problems: string[];
  limits: { max_queries_per_run: number; max_cost_per_run_usd: number; max_competitors: number; max_seed_topics: number };
}

export interface ProblemDetails {
  type?: string;
  title: string;
  status: number;
  detail?: string;
  request_id?: string;
  errors?: { location: string; field: string | null; message: string; type: string }[];
}
