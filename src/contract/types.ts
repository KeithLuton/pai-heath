/**
 * Engine contract — MEDICAL_PRIMITIVES.md v0.4.4.
 *
 * The UI renders these objects. It never computes a lock, a status, a lane,
 * a temporal resolution, a suggestion, or a notification. Those come from the
 * engine (or, during UI development, from a fixture/demo file).
 */

// ---------- Part I: record evidence ----------

export type ProvenanceLock = 'VERIFIED_SPAN' | 'UNVERIFIED_EXTRACTION' | 'CHECKER_DISCREPANCY' | 'FAILED';
export type ContentStatus = 'UNVERIFIED' | 'SPECULATIVE' | 'EXPLAINED' | 'VALIDATED';
export type Standing = 'FIRST_PERSON' | 'RELAYED' | 'CLINICAL' | 'DEVICE';

export type Speaker =
  | 'PATIENT' | 'CLINICIAN' | 'INTERPRETING_SPECIALIST' | 'LAB_INSTRUMENT'
  | 'DEVICE' | 'ADMINISTRATIVE' | 'FAMILY_OR_OTHER' | 'UNKNOWN';
export type Kind =
  | 'REPORT' | 'OBSERVATION' | 'MEASUREMENT' | 'INTERPRETATION'
  | 'ASSESSMENT' | 'CODE' | 'ACTION' | 'MEDICATION_STATUS';
export type Polarity = 'AFFIRMED' | 'NEGATED';
export type Certainty = 'DEFINITE' | 'HEDGED' | 'HYPOTHETICAL';
export type Temporality = 'CURRENT' | 'HISTORICAL' | 'PLANNED' | 'UNSPECIFIED';
export type Experiencer = 'PATIENT' | 'FAMILY' | 'OTHER';

export interface EventDate {
  value: string | null;
  precision: 'DAY' | 'MONTH' | 'YEAR' | 'APPROXIMATE' | 'UNKNOWN';
  source: 'DOCUMENTED' | 'ENCOUNTER_DATE' | 'DOCUMENT_DATE_FALLBACK' | string;
  meaning?: string;
  as_stated?: string;
}

export interface Provenance {
  document_id: string;
  document_sha256: string;
  page: number;
  char_start: number;
  char_end: number;
  author: string | null;
  author_role: string | null;
  signed_date: string | null;
  relay?: string;
}

export interface FloatFact {
  id: string;
  concept: string;
  original_text: string;
  speaker: Speaker;
  kind: Kind;
  polarity: Polarity;
  certainty: Certainty;
  temporality: Temporality;
  experiencer: Experiencer;
  event_date: EventDate;
  section?: string;
  provenance: Provenance;
  root: string;
  provenance_lock: ProvenanceLock;
  content_status: ContentStatus;
  standing?: Standing;
  medication?: { drug: string; dose_text?: string | null; frequency_text?: string | null; med_status: string; start_date?: string | null };
  action_state?: string;
  value?: { numeric: number; unit: string; reference_low?: number | null; reference_high?: number | null; reference_unit?: string | null; performing_lab?: string | null; collected_at?: string | null };
  range_comparison?: { result: string; basis: string };
  note?: string;
}

export interface MixRelation {
  id: string;
  relation: string;
  endpoints: string[];
  temporal?: { temporal_resolution: 'EXACT' | 'RANGE' | 'ORDER_ONLY' | 'INDETERMINATE'; order_basis?: string; delta_days: number | null; delta_days_min?: number | null; delta_days_max?: number | null; date_basis?: string[] };
  comparability?: { class: string; reasons: string[] };
  rendered?: string;
  content_status?: ContentStatus;
}

export interface GapRecord {
  id: string;
  sought: string[];
  search_scope: string[];
  date_window: [string, string];
  coverage: { documents_searched: number; documents_in_window: number };
  result: string;
  rendered: string;
}

export interface FlagRecord { id: string; type: string; cites: string[]; rendered: string }

export interface StirChainLink {
  link: string; from: string; to: string;
  source: { type: string; id: string; also?: string } | null;
  record_evidence?: string[];
  patient_specific_notes?: string[];
}

export interface StirItem { cites: string | string[]; link?: string; level?: string; basis?: string; checklist?: number; item?: string; status?: string; reason?: string; note?: string }

export interface StirMap {
  opening: { opener: string; form: string; verbatim: string };
  subject: { exposure: string[]; finding: string[]; rule: string };
  chain: StirChainLink[];
  direct_link: { from: string; to: string; source: unknown; note: string };
  support: StirItem[];
  challenge: StirItem[];
  non_discriminating: StirItem[];
  unplaced: StirItem[];
  content_status: ContentStatus;
}

export interface SourceDocument { date: string; title: string; text: string; sha256: string }

export interface Scenario {
  fixture_id: string;
  constitution_version: string;
  verification: { authored_by: string; verified_by: string | null; rule: string };
  documents: Record<string, SourceDocument>;
  authority_inputs: { id: string; status: string; text: string; level: string }[];
  expected: {
    floats: FloatFact[];
    mixes: MixRelation[];
    gaps: GapRecord[];
    flags: FlagRecord[];
    stir?: StirMap;
    rendering?: { reference_text: string };
  };
}

// ---------- Part II: tracker ----------

export type DataPath = 'REVIEW' | 'LIVE';
export type AiMode = 'NONE' | 'PAI' | 'PSI';

export interface Settings {
  data_path: DataPath;
  device_dose_confirmation: boolean;
  ai_mode: 'PAi' | 'PSi' | 'None';
  live_available: boolean;
  local_model_installed: boolean;
}

export interface LaneState { area: string; lane: 1 | 2 | 3 | null; summary: string; history: string; escalates_if: string | null; rule?: string }
export interface Prompt {
  id: string; kind: 'DOSE' | 'MEASURE'; text: string; options: string[]; safety_line: string | null;
  action?: { type: 'DOSE'; med_id: string; due: string } | { type: 'OPEN'; route: string }; rule?: string;
}

export interface Medication {
  id: string; name: string; dose: string;
  start: string | null; start_basis: string;
  adherence: string; sensor: string | null;
  verification: { state: 'QUALIFIED' | 'NOT_DETECTABLE' | 'LEARNING'; text: string };
  confirmation: { sensor_class: 'DIRECT_DELIVERY' | 'CONTAINER_ACCESS'; status_off: ContentStatus; status_on: ContentStatus; label: string } | null;
  schedule?: string[]; targets?: string[]; origin?: 'LOG' | 'RECORD';
}

export interface ChangeWindow {
  id: string; anchor: string; measure: string; target_set_by: string;
  pre: { days: number; readings: number; mean_sys: number; mean_dia: number; daily_spread_sys: number };
  post: { days: number; readings: number; mean_sys: number; mean_dia: number };
  difference_vs_spread: 'LARGER' | 'SMALLER' | 'SIMILAR';
  adherence: string;
  challenge: { item: string; text: string }[];
}

export interface Suggestion {
  id: string; area: string; title: string; text: string;
  state: 'ACTIVE' | 'HELD';
  effect?: string; source: string; verified: boolean;
  adopted?: boolean; adopted_on?: string; progress?: string; note?: string; held_reason?: string;
}

export interface Tracker {
  synthetic: boolean;
  notice: string;
  as_of: string;
  user: { display_name: string; age: number | null; sex: string | null };
  settings: Settings;
  lanes: LaneState[];
  prompts: Prompt[];
  notices: { id: string; text: string }[];
  medications: Medication[];
  change_windows: ChangeWindow[];
  suggestions: Suggestion[];
  raises_bp: { item: string; logged: string; text: string }[];
  labs: {
    annual_status: 'CURRENT' | 'OVERDUE';
    last_entered: string;
    ask_to_include: { test: string; why: string; source: string }[];
    unlocks: string[];
    entry_methods: string[];
  };
  nutrition: {
    date: string;
    meals: { time: string; label: string; method: string }[];
    totals: { nutrient: string; range: string; target: string; state: 'WITHIN' | 'BELOW' | 'ABOVE' | 'HELD' | 'INFO' }[];
    interaction_checks: { pair: string; state: string; text: string }[];
    excluded_note: string;
  };
  vitals: {
    bp: { id: string; date: string; slot: string; systolic: number; diastolic: number; device_class: string; standing: Standing }[];
    daily: { date: string; resting_hr: number | null; sleep_hours: number | null; steps: number | null; active_minutes: number | null; device_class: string }[];
    glucose: { date: string; fasting_mg_dl: number; device_class: string }[];
    lisinopril_start: string;
    markers?: { x: string; label: string }[];
  };
  audit: { at: string; event: string; rule: string }[];
}
