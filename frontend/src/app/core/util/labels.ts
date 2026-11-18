import { ChannelName, GapType, Intent, RunStatus, StageStatus } from '../api/models';

export const isTerminal = (status: RunStatus): boolean =>
  status === 'succeeded' || status === 'partial' || status === 'failed';

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Complete',
  partial: 'Partial results',
  failed: 'Failed',
};

export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  pending: 'Waiting',
  running: 'In progress',
  ok: 'Done',
  degraded: 'Done with gaps',
  failed: 'Failed',
  skipped: 'Skipped',
};

export const CHANNEL_LABEL: Record<ChannelName, string> = {
  organic: 'Google search',
  ai_overview: 'AI Overview',
  ai_answers: 'AI assistants',
};

export const ENGINE_LABEL: Record<string, string> = {
  chat_gpt: 'ChatGPT',
  perplexity: 'Perplexity',
  gemini: 'Gemini',
  claude: 'Claude',
};

export const engineLabel = (engine: string): string => ENGINE_LABEL[engine] ?? engine;

export const INTENT_LABEL: Record<Intent, string> = {
  informational: 'Informational',
  navigational: 'Navigational',
  commercial: 'Commercial',
  transactional: 'Transactional',
};

export const GAP_LABEL: Record<GapType, string> = {
  absent_everywhere: 'Absent everywhere',
  missing_in_ai_answers: 'Missing from AI answers',
  missing_in_ai_overview: 'Missing from AI Overview',
  ranking_low_organic: 'Ranking too low',
  competitor_dominated: 'Competitors own it',
  already_strong: 'Already strong',
};

export const CONTENT_TYPE_LABEL: Record<string, string> = {
  comparison_page: 'Comparison page',
  alternatives_page: 'Alternatives page',
  buyers_guide: "Buyer's guide",
  how_to_guide: 'How-to guide',
  faq_hub: 'FAQ hub',
  use_case_page: 'Use-case page',
  case_study: 'Case study',
  landing_page_update: 'Landing page update',
  third_party_listing: 'Third-party listing',
};

export const FACTOR_LABEL: Record<string, string> = {
  demand: 'Demand',
  gap: 'Visibility gap',
  urgency: 'Competitor pressure',
  winnability: 'Winnability',
  intent_value: 'Intent value',
};

export const DEGRADATION_NODE_LABEL: Record<string, string> = {
  discovery_planner: 'Research planning',
  discovery_retriever: 'Market research',
  discovery_normalizer: 'Question discovery',
  seed_fallback: 'Question discovery',
  measurement_planner: 'Question selection',
  measurement_retriever: 'Measurement',
  measurement_normalizer: 'Reading results',
  gap_analyst: 'Gap analysis',
  recommender: 'Recommendations',
};
