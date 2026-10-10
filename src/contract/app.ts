/**
 * App-level API: setup, users, AI, ingest. Same-origin, relative paths under the
 * per-run token, POSTs carry X-PAi-Request like settings do. Nothing here
 * computes anything; it carries what the engine decided.
 */

export interface HardwareProfile {
  total_ram_gb: number; available_ram_gb: number; free_disk_gb: number;
  cpu_count: number; architecture: string; gpu_name: string; vram_gb: number; backend: string;
}
export interface ModelOption {
  key: string; label: string; repo_id: string; size_gb: number; min_ram_gb: number;
  fits: boolean; recommended: boolean; license: string;
}
export interface ProviderOption { id: 'nemotron' | 'openai' | 'anthropic'; label: string; key_hint: string; keys_url: string }
export interface SystemInfo {
  hardware: HardwareProfile;
  runtime: string;
  analysis_models: ModelOption[];
  checkers: ModelOption[];
  installed: { analysis: Record<string, unknown>; checker: Record<string, unknown> };
  providers: ProviderOption[];
}
export interface AIState {
  mode?: 'None' | 'PAi' | 'PSi';
  local: { analysis: string | null; checker: string | null; runtime: boolean };
  external?: { provider: string; label: string; model: string; allow_pages: boolean; allow_values: boolean; allow_investigate?: boolean; key_set: boolean } | null;
}
export interface Job {
  id: string; kind: string; state: 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED';
  message: string; done: number; total: number; result: unknown; error: string | null; docs: string[];
}
export interface AppState {
  users: { id: string; name: string }[];
  user: { id: string; name: string } | null;
  ai: AIState;
  knowledge_base: { built_at: string; shards_included: string } | null;
  jobs: Job[];
}

export type DocStatus = 'CHECKING' | 'NOT_CHECKED_YET' | 'NEEDS_REVIEW' | 'NOT_CHECKED' | 'CHECKED';
export interface CheckSummary {
  pages_agree: number; pages_disagree: number; items_agree: number; items_disagree: number;
  items_cannot_tell: number; not_checked: number; failed: number; gate_flags: number;
}
export interface DocRow {
  doc_id: string; file_name: string; media_type: string; pages: number; date: string | null;
  summary: CheckSummary | null; open_issues: number; status: DocStatus;
  checkers: { pages: string | null; values: string | null } | null; warnings: string[];
  billing?: { profile_id: string | null; candidates: string[] };
}
export interface PageCheck {
  page: number; method: string;
  verdict: 'AGREES' | 'DISAGREES' | 'UNREADABLE' | 'NOT_CHECKED' | 'NO_PAGE_IMAGE' | 'CHECK_FAILED';
  differences: { text_says: string; page_shows: string }[]; note: string;
}
export interface ItemCheck {
  id: string; page: number; span: [number, number]; fields: Record<string, unknown>; source_line: string;
  verdict: 'AGREE' | 'DISAGREE' | 'CANNOT_TELL' | 'NOT_CHECKED' | 'CHECK_FAILED';
  source_says: string; reason: string; guard?: string | null;
}
export interface Issue {
  target: string; kind: 'PAGE' | 'GATE' | 'ITEM';
  page?: number; verdict?: string; differences?: PageCheck['differences']; note?: string;
  flags?: string[]; fields?: Record<string, unknown>; source_line?: string; source_says?: string; reason?: string; id?: string;
}
export interface DocDetail {
  doc_id: string; file_name: string; media_type: string; date: string | null; warnings: string[];
  pages: { number: number; method: string; text: string; start: number; end: number; flags: string[];
           ocr_confidence: number | null; has_image: boolean }[];
  report: { checked_at: string; checkers: { pages: string | null; values: string | null };
            gate: { page: number; flags: string[] }[]; pages: PageCheck[]; items: ItemCheck[]; summary: CheckSummary } | null;
  resolutions: Record<string, 'EXTRACTION_CORRECT' | 'EXTRACTION_WRONG'>;
  issues: Issue[];
  floats: { id: string; concept: string; kind: string; original_text: string; provenance_lock: string;
            lock_basis?: string; content_status: string; provenance: { page: number; char_start: number; char_end: number } }[];
  billing?: BillingState;
}

/* ------------------------------------------------------------------ billing (engine/billing) */
export type AssertionKind = 'submitted' | 'estimated' | 'billed' | 'adjudicated' | 'paid-as-reported' | 'bank-recorded';
export interface BillingLocator {
  page?: number; text_start?: number; text_end?: number; bbox?: [number, number, number, number]; bbox_unit?: string;
  json_pointer?: string; ocr_word_confidence_min?: number;
}
export interface BillingField {
  field: string; label: string | null; raw_value: string; normalized_value: string | null; source_id: string;
  locator: BillingLocator; row_id: string | null; assertion_kind: AssertionKind; issues: string[];
  currency?: string; currency_symbol?: string; sign_basis?: string; conflict_values?: string[];
}
export interface BillingRow { row_id: string; page?: number; kind?: string; pointer?: string; fields: BillingField[]; issues: string[] }
export interface BillingTable { table_id: string; page?: number; columns: { label: string; field: string }[]; rows: BillingRow[]; totals: BillingRow | null }
export interface BillingRecord {
  profile_id: string; title: string; family: string; description: string; standardization: string;
  adapter: string; adapter_status: string; locale: string | null; assertion_kind: AssertionKind;
  confirmation: { basis: 'PERSON' | 'STRUCTURAL'; by?: string | null; at?: string | null; detail?: string };
  header: BillingField[]; tables: BillingTable[]; issues: { code: string; message: string; field?: string; row_id?: string; rows?: string[] }[];
  notes: string[]; profile_notes: string[]; not_found?: string[]; resource_type?: string; fhir_version?: string;
}
export interface BillingState {
  candidates: { profile_id: string; title: string; supported: boolean }[];
  fhir: { profile_id: string; resource_type: string; pointer: string }[];
  form_fields_filled: number;
  confirmed: { doc_id: string; profile_id: string; by: string | null; at: string } | null;
  extraction: { doc_id: string; engine: string; records: BillingRecord[]; read_notes: string[] } | null;
}
export interface BillingProfile { profile_id: string; title: string; family: string; description: string; supported: boolean; confirm: boolean; status: string }

async function read<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error || `The engine returned ${res.status}.`);
  return body as T;
}

export const api = {
  get: <T,>(path: string) => fetch('./api/' + path, { cache: 'no-store' }).then(r => read<T>(r)),
  post: <T,>(path: string, body: unknown) => fetch('./api/' + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PAi-Request': '1' }, body: JSON.stringify(body ?? {}),
  }).then(r => read<T>(r)),
  upload: (file: File, extra: Record<string, string> = {}) => fetch('./api/ingest/upload', {
    method: 'POST', headers: { 'X-PAi-Request': '1', 'X-File-Name': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream',
      'X-Time-Zone': Intl.DateTimeFormat().resolvedOptions().timeZone, ...extra },
    body: file,
  }).then(r => read<{ documents: { doc_id: string; file_name: string; duplicate: boolean }[]; failures: { file_name: string; reason: string }[];
    job: string | null; device?: { kind: string; label: string; needs: string[] } }>(r)),
};

export async function waitJob(id: string, onUpdate: (j: Job) => void, everyMs = 700): Promise<Job> {
  for (;;) {
    const j = await api.get<Job>('jobs/' + id);
    onUpdate(j);
    if (j.state === 'DONE' || j.state === 'FAILED') return j;
    await new Promise(r => setTimeout(r, everyMs));
  }
}

export const gb = (n: number) => `${n.toFixed(n < 10 ? 1 : 0)} GB`;
