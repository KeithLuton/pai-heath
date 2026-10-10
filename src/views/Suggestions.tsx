import type { Tracker } from '../contract/types';
import { logEntry } from '../components/LogForms';

export function Suggestions({ t, live = false, onLogged }: { t: Tracker; live?: boolean; onLogged?: () => void }) {
  const adopt = async (id: string, active: boolean) => { await logEntry({ type: 'ADOPT', suggestion_id: id, active }); onLogged?.(); };
  const active = t.suggestions.filter(s => s.state === 'ACTIVE');
  const held = t.suggestions.filter(s => s.state === 'HELD');
  return (
    <div className="view">
      <header className="view-head">
        <h1>Suggestions</h1>
        <p className="lede">Each one comes from a cited guideline and passed a safety check against your medications and records. Effects are averages from studies of groups, not a promise for you.</p>
      </header>

      <section>
        <h2 className="section-title">For you now</h2>
        {t.suggestions.length === 0 && <p className="empty">Suggestions appear when your readings call for them — for example, a home blood-pressure average above 130/80.</p>}
        <ul className="suggestions">
          {active.map(s => (
            <li key={s.id} className={s.adopted ? 'sugg adopted' : 'sugg'}>
              <div className="sugg-head"><h3>{s.title}</h3>{s.adopted ? <span className="adopted-tag">Following since {s.adopted_on}{live && <> · <button className="link" onClick={() => adopt(s.id, false)}>stop</button></>}</span> : <button className="btn" disabled={!live} onClick={() => adopt(s.id, true)}>I'll try this</button>}</div>
              <p>{s.text}</p>
              {s.effect && <p className="muted">{s.effect}</p>}
              {s.progress && <p>{s.progress}</p>}
              {s.note && <p className="note">{s.note}</p>}
              <p className="source-line">Source: {s.source}{!s.verified && <span className="unverified-src"> (figure to be checked against the source before release)</span>}</p>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="section-title">Held for safety</h2>
        <ul className="suggestions">
          {held.map(s => (
            <li key={s.id} className="sugg held">
              <h3>{s.title}</h3>
              <p className="muted">{s.text}</p>
              <p>{s.held_reason}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
