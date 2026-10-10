import { useState } from 'react';
import type { Settings, Tracker } from '../contract/types';
import { LaneMark } from '../components/Status';
import { logEntry } from '../components/LogForms';

export function Today({ t, settings, go, live = false, onLogged }: { t: Tracker; settings: Settings; go: (r: string) => void; live?: boolean; onLogged?: () => void }) {
  const [answered, setAnswered] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const answer = async (p: Tracker['prompts'][number], o: string) => {
    if (p.action?.type === 'OPEN') { go(p.action.route); return; }
    if (live && p.action?.type === 'DOSE') {
      try {
        await logEntry({ type: 'DOSE', med_id: p.action.med_id, due: p.action.due, taken: o === 'Yes' ? 'YES' : o === 'No' ? 'NO' : 'NOT_SURE', at: o === 'Yes' ? p.action.due : undefined });
        onLogged?.();
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); return; }
    }
    setAnswered(a => ({ ...a, [p.id]: o }));
  };
  const adopted = t.suggestions.filter(s => s.adopted);
  return (
    <div className="view">
      <header className="view-head">
        <h1>{new Date(t.as_of + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</h1>
        <p className="lede">
          {settings.data_path === 'REVIEW'
            ? 'Review mode: prompts reflect data imported so far, not live sensor readings.'
            : 'Live mode: prompts follow your phone-synced data.'}
        </p>
      </header>

      <section className="lanes">
        {t.lanes.map(l => (
          <article key={l.area} className={`lane-row lane-row-${l.lane ?? 'none'}`}>
            <div className="lane-row-top">
              <h2>{l.area}</h2>
              <LaneMark lane={l.lane} />
            </div>
            <p className={l.lane === 3 ? 'lane-summary urgent' : 'lane-summary'}>{l.summary}</p>
            <p className="lane-history">{l.history}</p>
            {l.escalates_if && <p className="lane-escalate">A check-in prompt appears if: {l.escalates_if}</p>}
          </article>
        ))}
      </section>

      {t.notices.map(n => (
        <div key={n.id} className="notice">
          <p>{n.text}</p>
          <button className="btn-quiet" onClick={() => go('labs')}>See which labs to ask for</button>
        </div>
      ))}

      <section>
        <h2 className="section-title">Waiting on you</h2>
        {err && <p className="fatal-inline" role="alert">{err}</p>}
        {t.prompts.length === 0 && <p className="empty">Nothing right now.</p>}
        <ul className="prompts">
          {t.prompts.map(p => (
            <li key={p.id} className="prompt">
              <p>{p.text}</p>
              {answered[p.id] ? (
                <p className="prompt-done">Recorded: {answered[p.id]}. Logged by you.</p>
              ) : (
                <div className="prompt-actions">
                  {p.options.map(o => (
                    <button key={o} className={o === p.options[0] ? 'btn' : 'btn-quiet'}
                      onClick={() => answer(p, o)}>{o}</button>
                  ))}
                </div>
              )}
              {p.safety_line && <p className="safety-line">{p.safety_line}</p>}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="section-title">Habits you're following</h2>
        {adopted.length === 0 ? <p className="empty">No suggestions adopted yet. <button className="link" onClick={() => go('suggestions')}>Browse suggestions</button></p> : (
          <ul className="plain-list">
            {adopted.map(s => (
              <li key={s.id}><strong>{s.title}</strong> since {s.adopted_on}. {s.progress}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
