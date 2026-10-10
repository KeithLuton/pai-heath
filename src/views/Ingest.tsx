import { useCallback, useEffect, useRef, useState } from 'react';
import { api, waitJob, type AIState, type DocDetail, type DocRow, type Issue, type Job } from '../contract/app';

interface DeviceImport { import_id: string; label: string; file_name: string; saved: Record<string, number>; already_present: Record<string, number>; rejected: number; first: string | null; last: string | null; imported_at: string; importer_status: string; notes: string[] }
import { ProvLock } from '../components/Status';
import { BluetoothPanel } from './Bluetooth';
import { BillingPanel } from './Billing';
import type { ProvenanceLock } from '../contract/types';

const STATUS: Record<DocRow['status'], [string, string]> = {
  CHECKING: ['Checking…', 'st-run'],
  NOT_CHECKED_YET: ['Waiting', 'st-run'],
  NEEDS_REVIEW: ['Needs review', 'st-warn'],
  NOT_CHECKED: ['Not AI-checked', 'st-muted'],
  CHECKED: ['Checked', 'st-ok'],
};

const VERDICT: Record<string, string> = {
  AGREES: 'Page and text agree', DISAGREES: 'Page and text disagree', UNREADABLE: 'Checker couldn’t read the page',
  NOT_CHECKED: 'Not checked', NO_PAGE_IMAGE: 'Text format — no page image', CHECK_FAILED: 'Check didn’t complete',
  AGREE: 'Matches its line', DISAGREE: 'Doesn’t match its line', CANNOT_TELL: 'Checker unsure',
};

const FORMATS = 'Watch exports (Apple Health export.zip, Fitbit / Google Takeout zip, Garmin Activities.csv) · PDF (text or scanned) · photos and faxes (JPEG, PNG, TIFF) · portal downloads (FHIR, C-CDA / CCD, HL7) · Word, text, HTML, email (.eml) · CSV / Excel · ZIP of any of these';

export function Ingest({ ai, goSettings, onChanged }: { ai: AIState; goSettings: () => void; onChanged: () => void }) {
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [log, setLog] = useState<{ name: string; text: string; tone: string }[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => api.get<{ documents: DocRow[] }>('ingest/list').then(r => setDocs(r.documents)).catch(e => setErr(String(e))), []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!docs.some(d => d.status === 'CHECKING' || d.status === 'NOT_CHECKED_YET')) return;
    const t = setTimeout(refresh, 1500);
    return () => clearTimeout(t);
  }, [docs, refresh]);

  const [askUnits, setAskUnits] = useState<File | null>(null);
  const [imports, setImports] = useState<DeviceImport[]>([]);
  const refreshImports = () => api.get<{ imports: DeviceImport[] }>('devices/imports').then(r => setImports(r.imports)).catch(() => undefined);
  useEffect(() => { refreshImports(); }, []);

  type Up = Awaited<ReturnType<typeof api.upload>>;
  const sendDevice = async (f: File, extra: Record<string, string>, first?: Up) => {
    const r = first ?? await api.upload(f, extra);
    if (!r.device) return r;
    if (r.device.needs.includes('units')) { setAskUnits(f); setLog(l => [{ name: f.name, text: `${r.device!.label}: choose the units it was exported in.`, tone: 'warn' }, ...l.slice(1)]); return null; }
    setLog(l => [{ name: f.name, text: `${r.device!.label} — importing…`, tone: 'muted' }, ...l.slice(1)]);
    const j = await waitJob(r.job!, setJob, 1000);
    if (j.state === 'FAILED') setLog(l => [{ name: f.name, text: j.error ?? 'Import failed', tone: 'warn' }, ...l.slice(1)]);
    else {
      const res = j.result as DeviceImport;
      const saved = Object.values(res.saved).reduce((a, b) => a + b, 0), dup = Object.values(res.already_present).reduce((a, b) => a + b, 0);
      setLog(l => [{ name: f.name, text: `${saved.toLocaleString()} readings saved to your vault${dup ? `, ${dup.toLocaleString()} already there` : ''}${res.rejected ? `, ${res.rejected} couldn’t be read` : ''}.`, tone: 'ok' }, ...l.slice(1)]);
      refreshImports(); onChanged();
    }
    return null;
  };

  const send = async (files: FileList | File[]) => {
    setErr(null);
    for (const f of Array.from(files)) {
      setLog(l => [{ name: f.name, text: 'Reading…', tone: 'muted' }, ...l]);
      try {
        const r = await sendDevice(f, {});
        if (!r) continue;
        const added = r.documents.filter(d => !d.duplicate).length;
        const dup = r.documents.length - added;
        const text = [added && `${added} document${added > 1 ? 's' : ''} read`, dup && `${dup} already here`,
          r.failures.length && `${r.failures.length} couldn’t be read: ${r.failures.map(x => `${x.file_name} — ${x.reason}`).join('; ')}`]
          .filter(Boolean).join(' · ') || 'Nothing readable found';
        setLog(l => [{ name: f.name, text, tone: r.failures.length ? 'warn' : 'ok' }, ...l.slice(1)]);
        await refresh();
        onChanged();
        if (r.job) waitJob(r.job, setJob).then(() => { refresh(); onChanged(); });
      } catch (e) {
        setLog(l => [{ name: f.name, text: e instanceof Error ? e.message : String(e), tone: 'warn' }, ...l.slice(1)]);
      }
    }
  };

  const checkers = ai.mode === 'PAi' ? [ai.local.checker && `${ai.local.checker} (pages)`, (ai.local.analysis || ai.local.checker) && `${ai.local.analysis || ai.local.checker} (values)`].filter(Boolean)
    : ai.mode === 'PSi' && ai.external ? [ai.external.allow_pages && `${ai.external.label} · ${ai.external.model} (pages)`, ai.external.allow_values && `${ai.external.label} · ${ai.external.model} (values)`].filter(Boolean) : [];

  return (
    <div className="view">
      <header className="view-head">
        <h1>Ingest</h1>
        <p className="lede">Add your records here. Each file is read, the built-in tests check every page, and then AI double-checks what was read against the page itself. Anything that doesn’t line up waits for you below — nothing is changed without you.</p>
      </header>

      <div className={`aibar${checkers.length ? '' : ' off'}`}>
        {checkers.length
          ? <>Double-checking with {checkers.join(' and ')}.{ai.mode === 'PSi' && <span className="warn"> Health data leaves this computer for these checks.</span>}</>
          : <>AI double-checking is off — uploads get the built-in quality tests only. <button className="link" onClick={goSettings}>Set up AI</button></>}
      </div>

      <div className={`drop${drag ? ' over' : ''}`}
        onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={e => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) send(e.dataTransfer.files); }}>
        <p className="drop-title">Drop files here</p>
        <p className="muted">{FORMATS}</p>
        <button className="btn" onClick={() => input.current?.click()}>Choose files</button>
        <input ref={input} type="file" multiple hidden onChange={e => { if (e.target.files) send(e.target.files); e.target.value = ''; }} />
        <p className="muted small">Name files with the date first (2023-03-10 neurology.pdf) when the document doesn’t state its own date.</p>
      </div>

      {askUnits && (
        <div className="callout row">
          <span>{askUnits.name}: were distances exported in</span>
          {(['statute', 'metric'] as const).map(u => <button key={u} className="btn-quiet" onClick={async () => { const f = askUnits; setAskUnits(null); await sendDevice(f, { 'X-Units': u }); }}>{u === 'statute' ? 'miles' : 'kilometres'}</button>)}
        </div>
      )}

      {(log.length > 0 || job) && (
        <ul className="uplog">
          {job && (job.state === 'RUNNING' || job.state === 'QUEUED') && <li className="muted">{job.message || 'Double-checking…'}</li>}
          {log.slice(0, 6).map((l, i) => <li key={i}><strong>{l.name}</strong> <span className={l.tone}>{l.text}</span></li>)}
        </ul>
      )}
      {err && <p className="fatal-inline" role="alert">{err}</p>}

      <section>
        <h2 className="section-title">Your documents</h2>
        {docs.length === 0 ? <p className="empty">Nothing added yet.</p> : (
          <table className="facts docs">
            <thead><tr><th>Document</th><th>Date</th><th>Pages</th><th>Double-check</th></tr></thead>
            <tbody>
              {docs.map(d => (
                <tr key={d.doc_id} className={open === d.doc_id ? 'sel' : ''} onClick={() => setOpen(open === d.doc_id ? null : d.doc_id)} tabIndex={0}
                  onKeyDown={e => { if (e.key === 'Enter') setOpen(d.doc_id); }}>
                  <td>{d.file_name}{d.warnings.length > 0 && <span className="concept">{d.warnings.join(' · ')}</span>}
                    {d.billing?.profile_id ? <span className="concept">Billing document · read as {d.billing.profile_id}</span>
                      : d.billing && d.billing.candidates.length > 0 ? <span className="concept warn">Looks like a billing document — open to confirm</span> : null}</td>
                  <td className="nowrap">{d.date ?? <span className="muted">no date</span>}</td>
                  <td>{d.pages}</td>
                  <td>
                    <span className={`st ${STATUS[d.status][1]}`}>{STATUS[d.status][0]}{d.status === 'NEEDS_REVIEW' ? ` · ${d.open_issues}` : ''}</span>
                    {d.summary && <span className="concept">{summaryLine(d)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {open && <DocReview id={open} onChanged={() => { refresh(); onChanged(); }} />}

      {imports.length > 0 && (
        <section>
          <h2 className="section-title">Watch and device imports</h2>
          <table className="facts docs">
            <thead><tr><th>Export</th><th>Covers</th><th>Readings</th><th>Status</th></tr></thead>
            <tbody>{imports.map(i => (
              <tr key={i.import_id + i.imported_at}>
                <td>{i.file_name}<span className="concept">{i.label} · imported {i.imported_at.slice(0, 16).replace('T', ' ')}</span></td>
                <td className="nowrap">{i.first ? `${i.first.slice(0, 10)} – ${(i.last ?? '').slice(0, 10)}` : '—'}</td>
                <td>{Object.values(i.saved).reduce((a, b) => a + b, 0).toLocaleString()}</td>
                <td><span className="concept">{i.importer_status === 'UNVERIFIED_AGAINST_REAL_EXPORT' ? 'Importer not yet checked against a real export of this kind' : i.importer_status}</span></td>
              </tr>))}</tbody>
          </table>
        </section>
      )}

      <BluetoothPanel />
    </div>
  );
}

function summaryLine(d: DocRow) {
  const s = d.summary!;
  const parts = [];
  if (s.pages_agree || s.pages_disagree) parts.push(`pages: ${s.pages_agree} agree, ${s.pages_disagree} disagree`);
  if (s.items_agree || s.items_disagree || s.items_cannot_tell) parts.push(`values: ${s.items_agree} match, ${s.items_disagree} don’t, ${s.items_cannot_tell} unsure`);
  if (s.gate_flags) parts.push(`${s.gate_flags} page quality flag${s.gate_flags > 1 ? 's' : ''}`);
  if (s.failed) parts.push(`${s.failed} check${s.failed > 1 ? 's' : ''} didn’t complete`);
  return parts.join(' · ');
}

function DocReview({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [d, setD] = useState<DocDetail | null>(null);
  const [page, setPage] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setD(null); setPage(1); api.get<DocDetail>('ingest/doc/' + id).then(setD).catch(e => setErr(String(e))); }, [id]);

  if (err) return <p className="fatal-inline">{err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const pg = d.pages.find(p => p.number === page) ?? d.pages[0];
  const pageCheck = d.report?.pages.find(p => p.page === pg?.number);
  const items = d.report?.items ?? [];
  const lockOf = (fid: string) => d.floats.find(f => f.id === fid);

  const resolve = async (target: string, resolution: 'EXTRACTION_CORRECT' | 'EXTRACTION_WRONG') => {
    try { setD(await api.post<DocDetail>('ingest/resolve', { doc_id: d.doc_id, target, resolution })); onChanged(); }
    catch (e) { setErr(String(e)); }
  };
  const recheck = async () => {
    setBusy(true);
    try {
      const { job } = await api.post<{ job: string }>('ingest/recheck', { doc_id: d.doc_id });
      await waitJob(job, () => undefined);
      setD(await api.get<DocDetail>('ingest/doc/' + d.doc_id)); onChanged();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  return (
    <section className="review">
      <div className="row-between">
        <h2 className="section-title">{d.file_name}</h2>
        <button className="btn-quiet" onClick={recheck} disabled={busy}>{busy ? 'Checking…' : 'Check again'}</button>
      </div>
      {d.report && <p className="muted small">Checked {d.report.checked_at.replace('T', ' ').replace('+00:00', ' UTC')}{d.report.checkers.pages ? ` · pages: ${d.report.checkers.pages}` : ''}{d.report.checkers.values ? ` · values: ${d.report.checkers.values}` : ''}</p>}

      {d.issues.length > 0 && (
        <div className="issues">
          <h3>Waiting for you ({d.issues.length})</h3>
          {d.issues.map(i => <IssueCard key={i.target} i={i} resolve={resolve} show={(p) => setPage(p)} />)}
        </div>
      )}

      {d.pages.length > 1 && (
        <div className="doc-tabs">
          {d.pages.map(p => {
            const v = d.report?.pages.find(x => x.page === p.number)?.verdict;
            return <button key={p.number} className={p.number === pg.number ? 'tab on' : 'tab'} onClick={() => setPage(p.number)}>
              Page {p.number}{v === 'DISAGREES' ? ' ⚠' : ''}{p.flags.length ? ' •' : ''}</button>;
          })}
        </div>
      )}

      {pg && (
        <div className={pg.has_image ? 'compare' : 'compare single'}>
          {pg.has_image && (
            <figure className="pageimg">
              <img src={`./api/ingest/page/${d.doc_id}/${pg.number}`} alt={`Page ${pg.number} of ${d.file_name}`} />
            </figure>
          )}
          <div className="pagetext">
            <p className="muted small">
              Read by: {pg.method === 'OCR' ? `OCR${pg.ocr_confidence ? ` (confidence ${Math.round(pg.ocr_confidence)})` : ''}` : pg.method === 'TEXT_LAYER' ? 'the PDF’s own text' : pg.method.toLowerCase()}
              {pg.flags.length > 0 && <span className="warn"> · {pg.flags.join(', ').toLowerCase().replace(/_/g, ' ')}</span>}
              {pageCheck && <> · <span className={pageCheck.verdict === 'AGREES' ? 'ok' : pageCheck.verdict === 'DISAGREES' ? 'warn' : ''}>{VERDICT[pageCheck.verdict]}</span></>}
            </p>
            <pre className="source pagesrc">{pg.text || '[no text]'}</pre>
            {pageCheck?.note && <p className="muted small">{pageCheck.note}</p>}
          </div>
        </div>
      )}

      <BillingPanel d={d} onDoc={nd => { setD(nd); onChanged(); }} show={setPage} />

      {items.length > 0 && (
        <>
          <h3 className="sub">What was read from this document</h3>
          <table className="facts checks">
            <thead><tr><th className="col-stmt">Source line</th><th>Read as</th><th>Double-check</th><th>Lock</th></tr></thead>
            <tbody>
              {items.map(it => {
                const f = lockOf(it.id);
                return (
                  <tr key={it.id} className={it.verdict === 'DISAGREE' ? 'flag' : ''}>
                    <td><span className="quote small">{it.source_line}</span></td>
                    <td>{fieldsLine(it.fields)}</td>
                    <td><span className={it.verdict === 'AGREE' ? 'ok' : it.verdict === 'DISAGREE' ? 'warn' : 'muted'}>{VERDICT[it.verdict]}</span>
                      {it.reason && it.verdict !== 'AGREE' && <span className="concept">{it.reason}</span>}
                      {it.guard === 'QUOTE_NOT_IN_SOURCE' && <span className="concept">The checker quoted words that aren’t in the line, so its objection was set aside.</span>}</td>
                    <td>{f && <ProvLock lock={f.provenance_lock as ProvenanceLock} compact />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      <p className="note">The checker can only raise a question. It never changes a value, a date or a status; a person settles every disagreement, and that decision is written to the audit log.</p>
    </section>
  );
}

function fieldsLine(f: Record<string, unknown>) {
  const num = f.numeric ?? f.qualitative;
  const bits = [f.analyte ?? f.concept, num !== undefined && num !== null ? `${f.comparator ?? ''}${num}` : null, f.unit,
    f.reference_low !== undefined || f.reference_high !== undefined ? `(ref ${f.reference_low ?? ''}–${f.reference_high ?? ''}${f.reference_unit ? ' ' + f.reference_unit : ''})` : null,
    f.date ? `· ${f.date}` : null, f.polarity === 'NEGATED' ? '· stated as absent' : null];
  return bits.filter(x => x !== null && x !== undefined && x !== '').join(' ');
}

function IssueCard({ i, resolve, show }: { i: Issue; resolve: (t: string, r: 'EXTRACTION_CORRECT' | 'EXTRACTION_WRONG') => void; show: (p: number) => void }) {
  return (
    <div className="issue">
      {i.kind === 'ITEM' && (
        <>
          <p><strong>A value may have been read wrong.</strong> Read as: {fieldsLine(i.fields ?? {})}</p>
          <p className="quote small">“{i.source_line}”</p>
          {i.source_says && <p className="muted small">Checker points to: “{i.source_says}”{i.reason ? ` — ${i.reason}` : ''}</p>}
        </>
      )}
      {i.kind === 'PAGE' && (
        <>
          <p><strong>Page {i.page}: {i.verdict === 'UNREADABLE' ? 'the checker couldn’t read this page.' : 'the page and the text read from it disagree.'}</strong> <button className="link" onClick={() => show(i.page!)}>Show page</button></p>
          <ul className="plain-list small">{(i.differences ?? []).map((x, k) => <li key={k}>Text says “{x.text_says}” — page shows “{x.page_shows}”</li>)}</ul>
        </>
      )}
      {i.kind === 'GATE' && (
        <p><strong>Page {i.page}: built-in quality test flagged {(i.flags ?? []).join(', ').toLowerCase().replace(/_/g, ' ')}.</strong> <button className="link" onClick={() => show(i.page!)}>Show page</button></p>
      )}
      <div className="row">
        <button className="btn-quiet" onClick={() => resolve(i.target, 'EXTRACTION_CORRECT')}>{i.kind === 'ITEM' ? 'It was read right' : 'The text is right'}</button>
        <button className="btn-quiet" onClick={() => resolve(i.target, 'EXTRACTION_WRONG')}>{i.kind === 'ITEM' ? 'It was read wrong' : 'The text is wrong'}</button>
      </div>
    </div>
  );
}
