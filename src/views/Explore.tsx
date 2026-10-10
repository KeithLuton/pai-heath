import { useEffect, useState } from 'react';
import { api, waitJob, type Job } from '../contract/app';

interface Proposal {
  id: string; kind: 'HYPOTHESIS' | 'QUESTION' | 'TEST' | 'CONNECTION'; text: string; cites: string[];
  quotes: { id: string; text: string }[]; notes: string[];
  evidence: { id: string; type: string; polarity?: string; provenance_lock?: string; content_status?: string }[];
}
interface Inv {
  id: string; question: string; model: string; saved_at: string; stopped: string;
  steps: { n: number; action: Record<string, unknown>; result: Record<string, unknown> }[];
  proposals: Proposal[]; answer: { text: string; sentences: { text: string; cites: string[] }[] } | null;
}

const KIND: Record<Proposal['kind'], string> = { HYPOTHESIS: 'Idea', QUESTION: 'Question', TEST: 'Possible test', CONNECTION: 'Connection' };

function describe(a: Record<string, unknown>) {
  switch (a.tool) {
    case 'search_records': return `Searched records for “${a.query}”`;
    case 'get_record': return `Read ${a.id}`;
    case 'related': return `Looked at what connects to ${a.id}`;
    case 'literature': return `Checked published links for ${(a.terms as string[] | undefined)?.join(', ')}`;
    case 'propose': return `Proposed (${String(a.kind).toLowerCase()}): ${a.text}`;
    case 'finish': return 'Wrote an answer';
    default: return 'Unreadable step';
  }
}

/** Ask a question (opens the Parliament), or let the AI explore it. The AI's output stays a proposal. */
export function Explore({ current, select, onAsked }: { current: string | null; select: (id: string) => void; onAsked: () => void }) {
  const [q, setQ] = useState(current ?? '');
  const [list, setList] = useState<Inv[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const refresh = () => api.get<{ investigations: Inv[] }>('investigations').then(r => setList(r.investigations)).catch(() => undefined);
  useEffect(() => { refresh(); }, []);
  const busy = job && (job.state === 'QUEUED' || job.state === 'RUNNING');

  const ask = async (text: string) => {
    setErr(null);
    try { await api.post('question', { text }); setAsked(text); onAsked(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const explore = async () => {
    setErr(null);
    try {
      const { job: id } = await api.post<{ job: string }>('investigate', { question: q });
      const j = await waitJob(id, setJob, 900);
      if (j.state === 'FAILED') setErr(j.error);
      refresh();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const cite = (id: string) => id.startsWith('F-')
    ? <button key={id} className="cite" onClick={() => select(id)}>{id}</button>
    : <span key={id} className="cite">{id}</span>;

  return (
    <section className="explore">
      <h2 className="section-title">Ask about your records</h2>
      <div className="row">
        <input className="qinput" value={q} onChange={e => setQ(e.target.value)} placeholder="e.g. Could my metformin be connected to the numbness in my feet?" />
        <button className="btn" disabled={!q.trim()} onClick={() => ask(q.trim())}>Ask</button>
        <button className="btn-quiet" disabled={!q.trim() || !!busy} onClick={explore}>{busy ? 'Exploring…' : 'Explore with AI'}</button>
      </div>
      <p className="muted small"><strong>Ask</strong> runs the evidence map above on your question. <strong>Explore with AI</strong> lets the AI search your records and the literature freely and suggest ideas — its findings stay unverified ideas until you ask one as your question.</p>
      {busy && <p className="muted">{job?.message || 'Starting…'}</p>}
      {asked && <p className="ok">Asked: “{asked}”. The evidence map above now answers it.</p>}
      {err && <p className="fatal-inline" role="alert">{err}</p>}

      {list.map(inv => (
        <article key={inv.id} className="inv">
          <p className="question-opener">AI exploration · {inv.model} · {inv.saved_at.replace('T', ' ').replace('+00:00', ' UTC')}</p>
          <h3>{inv.question}</h3>
          {inv.answer ? (
            <div className="inv-answer">
              <span className="bracket bracket-speculative"><span className="bracket-mark">[[</span><span className="bracket-word">AI, unverified</span><span className="bracket-mark">]]</span></span>
              {inv.answer.sentences.map((s, i) => <p key={i}>{s.text.replace(/\s*\[[A-Z]+-[A-Za-z0-9-]+\]/g, '')} {s.cites.map(cite)}</p>)}
            </div>
          ) : <p className="warn">The AI stopped before writing an answer it could cite ({inv.stopped === 'STEP_LIMIT' ? 'step limit' : inv.stopped}). Its ideas are below.</p>}
          {inv.proposals.length > 0 && (
            <>
              <h4>Ideas to look into</h4>
              <ul className="proposals">
                {inv.proposals.map(p => (
                  <li key={p.id}>
                    <span className="ptag">{KIND[p.kind]}</span> {p.text} {p.cites.map(cite)}
                    {p.notes.length > 0 && <span className="concept">{p.notes.join(' ')}</span>}
                    {(p.kind === 'QUESTION' || p.kind === 'HYPOTHESIS' || p.kind === 'CONNECTION') &&
                      <button className="link" onClick={() => ask(p.text.endsWith('?') ? p.text : `Could ${p.text.replace(/\.$/, '').replace(/^\w/, c => c.toLowerCase())}?`)}>Ask this as my question</button>}
                  </li>
                ))}
              </ul>
            </>
          )}
          <details>
            <summary>How it got there ({inv.steps.length} steps)</summary>
            <ol className="steps-log">
              {inv.steps.map(s => (
                <li key={s.n} className={'error' in s.result || s.result.accepted === false ? 'bounced' : ''}>
                  {describe(s.action)}
                  {'errors' in s.result && <span className="concept">Sent back: {(s.result.errors as string[]).join('; ')}</span>}
                  {'error' in s.result && <span className="concept">{String(s.result.error)}</span>}
                </li>
              ))}
            </ol>
          </details>
        </article>
      ))}
    </section>
  );
}
