import { useEffect, useState } from 'react';
import { DataNotice, NOTICE_VERSION } from '../components/About';
import { MedicalLibraries } from './MedicalLibraries';
import { api, gb, waitJob, type AIState, type AppState, type Job, type ModelOption, type ProviderOption, type SystemInfo } from '../contract/app';

/* ------------------------------------------------------------------ hardware */
export function HardwareCard({ s }: { s: SystemInfo }) {
  const h = s.hardware;
  return (
    <dl className="hw">
      <dt>Memory</dt><dd>{gb(h.total_ram_gb)} total · {gb(h.available_ram_gb)} available</dd>
      <dt>Storage</dt><dd>{gb(h.free_disk_gb)} free</dd>
      <dt>Processor</dt><dd>{h.cpu_count} logical cores · {h.architecture}</dd>
      <dt>Graphics</dt><dd>{h.gpu_name || 'None found — models run on the processor'}{h.vram_gb ? ` · ${gb(h.vram_gb)} VRAM` : ''}</dd>
    </dl>
  );
}

const CHECKER_OFF = 'Double-checking scans will be OFF. Pages still get the built-in quality tests, but nothing compares a scanned page with the text read from it, so a misread number can reach your records unnoticed.';

/* ------------------------------------------------------------------ private AI */
function PrivateAI({ s, onInstalled }: { s: SystemInfo; onInstalled: () => void }) {
  const fits = s.analysis_models.filter(m => m.fits);
  const checkers = s.checkers.filter(m => m.fits);
  const [model, setModel] = useState(fits.find(m => m.recommended)?.key ?? fits[0]?.key ?? '');
  const [wantChecker, setWantChecker] = useState(checkers.length > 0);
  const [checker, setChecker] = useState(checkers.find(m => m.recommended)?.key ?? checkers[0]?.key ?? '');
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const installedA = (s.installed.analysis as { model_label?: string; files_present?: boolean });
  const installedC = (s.installed.checker as { model_label?: string; files_present?: boolean });

  if (!fits.length) {
    return <p className="warn">This computer doesn't have enough free memory and disk for the smallest private model. Use an API key instead, or continue without AI — everything else still works.</p>;
  }
  const size = (list: ModelOption[], k: string) => list.find(m => m.key === k)?.size_gb ?? 0;

  const install = async () => {
    if (!wantChecker && !window.confirm(CHECKER_OFF + '\n\nInstall without the page checker? You can add it later in Settings.')) return;
    setErr(null);
    try {
      const { job: id } = await api.post<{ job: string }>('ai/install', { model, checker: wantChecker ? checker : null });
      const done = await waitJob(id, setJob);
      if (done.state === 'FAILED') setErr(done.error); else onInstalled();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const busy = job && (job.state === 'QUEUED' || job.state === 'RUNNING');

  return (
    <div className="ai-panel">
      {(installedA?.files_present || installedC?.files_present) && (
        <p className="ok">Installed: {[installedA?.files_present && installedA.model_label, installedC?.files_present && installedC.model_label].filter(Boolean).join(' and ')}.</p>
      )}
      {!s.runtime && (
        <div className="callout">
          <p><strong>One more piece is needed:</strong> the llama.cpp runtime that runs models on this computer. Install it once in PowerShell, then come back:</p>
          <pre className="cmd">winget install ggml.llamacpp</pre>
        </div>
      )}
      <label className="field">
        <span>Analysis model <span className="muted">— words what the engine found and double-checks extracted values</span></span>
        <select value={model} onChange={e => setModel(e.target.value)} disabled={!!busy}>
          {s.analysis_models.map(m => (
            <option key={m.key} value={m.key} disabled={!m.fits}>
              {m.label} · {gb(m.size_gb)}{m.recommended ? ' · recommended for this computer' : ''}{m.fits ? '' : ' · needs more memory'}
            </option>
          ))}
        </select>
      </label>
      <div className="checker">
        <label className="choice">
          <input type="checkbox" checked={wantChecker} disabled={!checkers.length || !!busy} onChange={e => setWantChecker(e.target.checked)} />
          <span><strong>Also install the page checker (recommended).</strong> A second, vision model whose only job is to look at each scanned page beside the text read from it and point out what disagrees — a number read wrong, a scrambled column, a handwritten note that never made it into the text.</span>
        </label>
        {wantChecker && checkers.length > 0 && (
          <label className="field indent">
            <select value={checker} onChange={e => setChecker(e.target.value)} disabled={!!busy}>
              {checkers.map(m => <option key={m.key} value={m.key}>{m.label} · {gb(m.size_gb)}{m.recommended ? ' · recommended' : ''}</option>)}
            </select>
          </label>
        )}
        {(!wantChecker || !checkers.length) && <p className="warn indent">{!checkers.length ? 'Not enough memory or disk for a page checker here. ' : ''}{CHECKER_OFF}</p>}
      </div>
      <p className="muted">Download: about {gb(size(s.analysis_models, model) + (wantChecker ? size(s.checkers, checker) : 0))}, once. Files are verified against the publisher's SHA-256. After that nothing leaves this computer.</p>
      {job && <Progress job={job} />}
      {err && <p className="fatal-inline" role="alert">{err}</p>}
      <button className="btn" onClick={install} disabled={!!busy || !model || !s.runtime}>{busy ? 'Installing…' : 'Download and install'}</button>
    </div>
  );
}

function Progress({ job }: { job: Job }) {
  const pct = job.total ? Math.round((job.done * 100) / job.total) : null;
  return (
    <div className="progress">
      <div className="bar"><div style={{ width: `${pct ?? (job.state === 'DONE' ? 100 : 8)}%` }} /></div>
      <p className="muted">{job.state === 'FAILED' ? `Stopped: ${job.error}` : job.message || job.state.toLowerCase()}{pct !== null && job.total > 2 ** 20 ? ` · ${(job.done / 2 ** 30).toFixed(2)} of ${(job.total / 2 ** 30).toFixed(2)} GB` : ''}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ external AI */
type Perm = 'allow_pages' | 'allow_values' | 'allow_investigate';
export interface ExternalDraft { provider: string; api_key: string; model: string; allow_pages: boolean; allow_values: boolean; allow_investigate: boolean; consent?: string }

function ExternalAI({ providers, draft, setDraft, current }: {
  providers: ProviderOption[]; draft: ExternalDraft; setDraft: (d: ExternalDraft) => void; current?: AIState['external'];
}) {
  const [models, setModels] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const p = providers.find(x => x.id === draft.provider);
  const [asking, setAsking] = useState<{ field: Perm; text: string; version: string } | null>(null);
  const toggle = async (field: Perm, on: boolean) => {
    if (!on) { setDraft({ ...draft, [field]: false }); return; }
    try {
      const c = await api.get<{ version: string; text: string }>('ai/consent/' + draft.provider);
      setAsking({ field, ...c });
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const load = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ models: string[]; suggested: string }>('ai/external/models', { provider: draft.provider, api_key: draft.api_key });
      setModels(r.models);
      setDraft({ ...draft, model: draft.model && r.models.includes(draft.model) ? draft.model : r.suggested });
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setModels([]); }
    setBusy(false);
  };
  return (
    <div className="ai-panel">
      <div className="providers" role="radiogroup" aria-label="Provider">
        {providers.map(pr => (
          <label key={pr.id} className={`provider${draft.provider === pr.id ? ' on' : ''}`}>
            <input type="radio" name="provider" checked={draft.provider === pr.id}
              onChange={() => { setDraft({ ...draft, provider: pr.id, model: '' }); setModels([]); }} />
            <span>{pr.label}</span>
          </label>
        ))}
      </div>
      {p && (
        <>
          <label className="field">
            <span>API key <span className="muted">— from {p.keys_url.replace('https://', '')}</span>{current?.provider === draft.provider && current.key_set ? <span className="muted"> · a key is saved; leave blank to keep it</span> : null}</span>
            <div className="row">
              <input type="password" autoComplete="off" placeholder={p.key_hint} value={draft.api_key} onChange={e => setDraft({ ...draft, api_key: e.target.value })} />
              <button className="btn-quiet" onClick={load} disabled={busy || draft.api_key.length < 8}>{busy ? 'Checking…' : 'Check key and load models'}</button>
            </div>
          </label>
          {err && <p className="fatal-inline" role="alert">{err}</p>}
          {(models.length > 0 || draft.model) && (
            <label className="field">
              <span>Model</span>
              <select value={draft.model} onChange={e => setDraft({ ...draft, model: e.target.value })}>
                {(models.length ? models : [draft.model]).map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
          )}
          <fieldset className="perms">
            <legend>What {p.label} may see</legend>
            <label className="choice">
              <input type="checkbox" checked={draft.allow_pages} onChange={e => toggle('allow_pages', e.target.checked)} />
              <span><strong>Page images</strong> of scanned and PDF documents, with the text read from them.</span>
            </label>
            <label className="choice">
              <input type="checkbox" checked={draft.allow_values} onChange={e => toggle('allow_values', e.target.checked)} />
              <span><strong>The lines</strong> each extracted value came from — not whole documents.</span>
            </label>
            <label className="choice">
              <input type="checkbox" checked={draft.allow_investigate} onChange={e => toggle('allow_investigate', e.target.checked)} />
              <span><strong>Investigate my questions</strong> — the records and source text it looks up while exploring a question you ask.</span>
            </label>
            {asking && (
              <div className="consent" role="dialog" aria-modal="true" aria-labelledby="consent-title">
                <p id="consent-title"><strong>Health data will leave this computer</strong></p>
                <p>{asking.text}</p>
                <div className="row">
                  <button className="btn" onClick={() => { setDraft({ ...draft, [asking.field]: true, consent: asking.version }); setAsking(null); }}>I agree — turn it on</button>
                  <button className="btn-quiet" onClick={() => setAsking(null)}>Keep it off</button>
                </div>
              </div>
            )}
            <p className="warn">{draft.allow_pages || draft.allow_values || draft.allow_investigate
              ? `Health data leaves this computer and goes to ${p.label} for what you switched on. Every call is written to your audit log.`
              : `With both off, ${p.label} sees no document content and uploads aren't AI-checked. It can still word the engine's findings.`}</p>
          </fieldset>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ AI choice (wizard + settings) */
type Path = 'private' | 'external' | 'none';

export function AIChoice({ s, ai, unlocked, onSaved, compact = false }: {
  s: SystemInfo; ai?: AIState; unlocked: boolean; onSaved: (path: Path, ext?: ExternalDraft) => void; compact?: boolean;
}) {
  const fitsAny = s.analysis_models.some(m => m.fits);
  const [path, setPath] = useState<Path>(ai?.mode === 'PSi' ? 'external' : fitsAny ? 'private' : 'external');
  const [draft, setDraft] = useState<ExternalDraft>({
    provider: ai?.external?.provider ?? 'nemotron', api_key: '', model: ai?.external?.model ?? '',
    allow_pages: ai?.external?.allow_pages ?? false, allow_values: ai?.external?.allow_values ?? false,
    allow_investigate: ai?.external?.allow_investigate ?? false,
  });
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const saveExternal = async () => {
    setErr(null);
    try {
      if (unlocked) {
        await api.post('ai/external/save', draft);
        await fetch('./api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PAi-Request': '1' }, body: JSON.stringify({ key: 'ai_mode', value: 'PSi' }) });
        setSaved(true);
      }
      onSaved('external', draft);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="aichoice">
      {!compact && <HardwareCard s={s} />}
      <div className="paths" role="radiogroup" aria-label="How PAi Health uses AI">
        <PathCard on={path === 'private'} pick={() => setPath('private')} title="Private AI on this computer"
          tag={fitsAny ? 'Recommended' : 'Not enough memory here'} tagTone={fitsAny ? 'good' : 'warn'}
          text="Models run here. Nothing leaves this computer after a one-time download." />
        <PathCard on={path === 'external'} pick={() => setPath('external')} title="Your own API key"
          tag="Nemotron · GPT · Claude" text="Uses NVIDIA, OpenAI or Anthropic with your key. Health data leaves this computer only if you allow it below." />
        <PathCard on={path === 'none'} pick={() => setPath('none')} title="No AI for now"
          text="Everything still works with the built-in wording. Uploads get the built-in quality tests only." />
      </div>
      {path === 'private' && <PrivateAI s={s} onInstalled={() => onSaved('private')} />}
      {path === 'external' && (
        <>
          <ExternalAI providers={s.providers} draft={draft} setDraft={setDraft} current={ai?.external} />
          {err && <p className="fatal-inline" role="alert">{err}</p>}
          <button className="btn" disabled={!draft.model || (!draft.api_key && !(ai?.external?.key_set && ai.external.provider === draft.provider))} onClick={saveExternal}>
            {unlocked ? 'Save and use this provider' : 'Use this provider'}
          </button>
          {saved && <span className="ok inline"> Saved.</span>}
        </>
      )}
      {path === 'none' && <button className="btn" onClick={() => onSaved('none')}>Continue without AI</button>}
    </div>
  );
}

function PathCard({ on, pick, title, text, tag, tagTone }: { on: boolean; pick: () => void; title: string; text: string; tag?: string; tagTone?: 'good' | 'warn' }) {
  return (
    <label className={`path${on ? ' on' : ''}`}>
      <input type="radio" name="aipath" checked={on} onChange={pick} />
      <span className="path-title">{title}{tag && <span className={`path-tag ${tagTone ?? ''}`}>{tag}</span>}</span>
      <span className="path-text">{text}</span>
    </label>
  );
}

/* ------------------------------------------------------------------ user forms */
function RestoreForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [bpw, setBpw] = useState('');
  const [vpw, setVpw] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const r = await fetch('./api/backup/restore', {
        method: 'POST', body: file,
        headers: { 'X-PAi-Request': '1', 'Content-Type': 'application/octet-stream',
          'X-User-Name': encodeURIComponent(name), 'X-Backup-Passphrase': encodeURIComponent(bpw), 'X-Vault-Passphrase': encodeURIComponent(vpw) },
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as { error?: string }).error || `Restore failed (${r.status})`);
      onDone();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <form className="userform" onSubmit={e => { e.preventDefault(); go(); }}>
      <label className="field"><span>Backup file <span className="muted">— usually in Documents\PAi Health Backups</span></span><input type="file" accept=".paihbkp" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <label className="field"><span>Name to show</span><input value={name} onChange={e => setName(e.target.value)} /></label>
      <label className="field"><span>Backup passphrase</span><input type="password" value={bpw} onChange={e => setBpw(e.target.value)} autoComplete="off" /></label>
      <label className="field"><span>Passphrase for the records in the backup <span className="muted">— the one you used to open them</span></span><input type="password" value={vpw} onChange={e => setVpw(e.target.value)} autoComplete="current-password" /></label>
      <p className="muted small">The backup is checked file by file and the audit log is verified before anything opens.</p>
      {err && <p className="fatal-inline" role="alert">{err}</p>}
      <div className="row">
        <button className="btn" type="submit" disabled={busy || !file || !bpw || !vpw}>{busy ? 'Restoring…' : 'Restore and open'}</button>
        <button className="btn-quiet" type="button" onClick={onCancel}>Back</button>
      </div>
    </form>
  );
}

function NewUser({ onCreated, onCancel }: { onCreated: () => void; onCancel?: () => void }) {
  const [restoring, setRestoring] = useState(false);
  if (restoring) return <RestoreForm onDone={onCreated} onCancel={() => setRestoring(false)} />;
  return <NewUserForm onCreated={onCreated} onCancel={onCancel} onRestore={() => setRestoring(true)} />;
}

function NewUserForm({ onCreated, onCancel, onRestore }: { onCreated: () => void; onCancel?: () => void; onRestore: () => void }) {
  const [name, setName] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [agree, setAgree] = useState(false);
  const create = async () => {
    if (pw !== pw2) { setErr("The passphrases don't match."); return; }
    if (!agree) { setErr('Please read and accept the notice first.'); return; }
    setBusy(true); setErr(null);
    try { await api.post('users/create', { name, passphrase: pw, notice: NOTICE_VERSION }); onCreated(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <form className="userform" onSubmit={e => { e.preventDefault(); create(); }}>
      <label className="field"><span>Name</span><input value={name} onChange={e => setName(e.target.value)} autoFocus /></label>
      <label className="field"><span>Passphrase <span className="muted">— at least 12 characters</span></span><input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="new-password" /></label>
      <label className="field"><span>Passphrase again</span><input type="password" value={pw2} onChange={e => setPw2(e.target.value)} autoComplete="new-password" /></label>
      <p className="warn">Your records are encrypted with this passphrase. There is no reset: if it's lost, the data can't be recovered. Write it down somewhere safe.</p>
      <DataNotice />
      <label className="choice">
        <input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} />
        <span>I understand, and I agree that PAi Health stores and processes my health information on this computer as described above.</span>
      </label>
      {err && <p className="fatal-inline" role="alert">{err}</p>}
      <div className="row">
        <button className="btn" type="submit" disabled={busy || !agree || !name.trim() || pw.length < 12}>{busy ? 'Creating…' : 'Create and open'}</button>
        {onCancel && <button className="btn-quiet" type="button" onClick={onCancel}>Back</button>}
        <button className="btn-quiet" type="button" onClick={onRestore}>Restore from a backup</button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ first run */
export function SetupWizard({ onReady }: { onReady: () => void }) {
  const [step, setStep] = useState(0);
  const [s, setS] = useState<SystemInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ext, setExt] = useState<ExternalDraft | null>(null);

  useEffect(() => {
    api.get<SystemInfo>('system').then(x => { setS(x); setStep(1); }).catch(e => setErr(String(e)));
  }, []);

  const afterUser = async () => {
    if (ext) {
      try {
        await api.post('ai/external/save', ext);
        await fetch('./api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PAi-Request': '1' }, body: JSON.stringify({ key: 'ai_mode', value: 'PSi' }) });
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    }
    setStep(3);
  };

  return (
    <div className="setup">
      <header className="setup-head">
        <div className="brand"><span className="brand-pai">PAi</span> Health</div>
        <span className="setup-badge">SETUP</span>
      </header>
      <div className="setup-body">
        <ol className="steps">
          {['Check this computer', 'Choose AI', 'Create your user', 'Medical libraries', 'Ready'].map((t, i) => (
            <li key={t} className={i <= step ? 'on' : ''}><span>{i + 1}</span>{t}</li>
          ))}
        </ol>
        {err && <p className="fatal-inline" role="alert">{err}</p>}
        {step === 0 && <section className="card"><h1>Checking this computer…</h1><p className="muted">Memory, storage, processor and graphics decide which private AI models can run here.</p></section>}
        {step === 1 && s && (
          <section className="card">
            <h1>How should PAi Health use AI?</h1>
            <p className="muted">AI only words what the engine found and double-checks what it reads from your documents. It never decides a status, an alert or a suggestion.</p>
            <AIChoice s={s} unlocked={false} onSaved={(p, d) => { setExt(p === 'external' ? d ?? null : null); setStep(2); }} />
          </section>
        )}
        {step === 2 && (
          <section className="card">
            <h1>Create your user</h1>
            <p className="muted">Each person gets their own encrypted vault on this computer.</p>
            <NewUser onCreated={afterUser} />
          </section>
        )}
        {step === 3 && <MedicalLibraries onDone={() => setStep(4)} />}
        {step === 4 && (
          <section className="card">
            <h1>Ready</h1>
            <p>Start in <strong>Ingest</strong>: add your records — portal downloads, PDFs, scans, photos of results. Each one is read, then double-checked, and anything that doesn't line up waits for you there.</p>
            <button className="btn" onClick={onReady}>Open PAi Health</button>
          </section>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ returning */
export function UnlockScreen({ state, onReady }: { state: AppState; onReady: () => void }) {
  const [uid, setUid] = useState(state.users[0]?.id ?? '');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const unlock = async () => {
    setBusy(true); setErr(null);
    try { await api.post('users/unlock', { id: uid, passphrase: pw }); onReady(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <div className="setup">
      <header className="setup-head"><div className="brand"><span className="brand-pai">PAi</span> Health</div></header>
      <div className="setup-body narrow">
        {creating ? (
          <section className="card"><h1>New user</h1><NewUser onCreated={onReady} onCancel={() => setCreating(false)} /></section>
        ) : (
          <section className="card">
            <h1>Open your records</h1>
            <form onSubmit={e => { e.preventDefault(); unlock(); }}>
              <label className="field"><span>User</span>
                <select value={uid} onChange={e => setUid(e.target.value)}>
                  {state.users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </label>
              <label className="field"><span>Passphrase</span><input type="password" value={pw} onChange={e => setPw(e.target.value)} autoFocus autoComplete="current-password" /></label>
              {err && <p className="fatal-inline" role="alert">{err}</p>}
              <div className="row">
                <button className="btn" type="submit" disabled={busy || !pw}>{busy ? 'Opening…' : 'Open'}</button>
                <button className="btn-quiet" type="button" onClick={() => setCreating(true)}>New user</button>
              </div>
            </form>
            <p className="muted small">AI: {state.ai.local.analysis || state.ai.local.checker ? `private models installed (${[state.ai.local.analysis, state.ai.local.checker].filter(Boolean).join(', ')})` : 'no private model installed'}. {state.knowledge_base ? `Medical literature index: ${state.knowledge_base.shards_included} PubMed shards.` : 'No medical literature index installed.'}</p>
          </section>
        )}
      </div>
    </div>
  );
}
