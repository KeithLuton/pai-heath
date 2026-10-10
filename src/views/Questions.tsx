import type { Scenario, StirItem } from '../contract/types';
import { Bracket } from '../components/Status';

function Cites({ c, s, select }: { c: string | string[]; s: Scenario; select: (id: string) => void }) {
  const ids = Array.isArray(c) ? c : [c];
  const floatIds = new Set(s.expected.floats.map(f => f.id));
  const auth = Object.fromEntries(s.authority_inputs.map(a => [a.id, a]));
  return (
    <span className="cites">
      {ids.map(id => {
        const base = id.split('.')[0];
        if (floatIds.has(base)) return <button key={id} className="cite" onClick={() => select(base)}>{id}</button>;
        if (auth[id]) return <span key={id} className="cite cite-auth" title={auth[id].text}>{id}{auth[id].status.includes('STUB') ? ' (test stub)' : ''}</span>;
        return <span key={id} className="cite">{id}</span>;
      })}
    </span>
  );
}

function ItemList({ items, s, select, empty }: { items: StirItem[]; s: Scenario; select: (id: string) => void; empty: string }) {
  if (!items.length) return <p className="empty">{empty}</p>;
  return (
    <ul className="stir-items">
      {items.map((it, i) => (
        <li key={i}>
          {it.item && <strong>{it.item}. </strong>}
          {it.link && <span className="muted">Link {it.link}{it.level ? `, ${it.level.toLowerCase()}-level` : ''}{it.basis === 'ENGINE_COMPUTED' ? ', computed by PAi' : ''}. </span>}
          {it.status === 'NOT_ADDRESSED_NO_SOURCE' && <span className="muted">No attached source addresses this. </span>}
          {it.reason && <span>{it.reason} </span>}
          {it.note && <span className="muted">{it.note} </span>}
          {it.cites && <Cites c={it.cites} s={s} select={select} />}
        </li>
      ))}
    </ul>
  );
}

export function Questions({ s, select }: { s: Scenario; select: (id: string) => void }) {
  const st = s.expected.stir;
  if (!st) {
    return (
      <div className="view">
        <header className="view-head">
          <h1>Your questions</h1>
          <p className="lede">No question has been asked about these records yet. A question is opened only by you or by your clinician's own words.</p>
        </header>
      </div>
    );
  }
  return (
    <div className="view">
      <header className="view-head">
        <h1>Your questions</h1>
        <p className="lede">A question is opened only by you or by your clinician's own words. PAi maps the evidence on both sides and stops there; it never gives a verdict.</p>
      </header>

      <article className="question">
        <p className="question-opener">Asked by {st.opening.opener === 'USER' ? 'you' : 'your clinician'}, stored word for word</p>
        <h2 className="question-text">“{st.opening.verbatim}”</h2>
        <p className="muted">Overall status of this map: <Bracket status={st.content_status} /></p>

        <section>
          <h3>Steps this question depends on</h3>
          <ol className="chain">
            {st.chain.map(l => (
              <li key={l.link}>
                <p><strong>{l.from} → {l.to}</strong></p>
                <p className="muted">Source of this step: {l.source ? <Cites c={l.source.id} s={s} select={select} /> : 'none attached'}</p>
                {l.record_evidence && l.record_evidence.length > 0 && <p className="muted">In your records: <Cites c={l.record_evidence} s={s} select={select} /></p>}
                {l.patient_specific_notes?.map((n, i) => <p key={i} className="note">{n}</p>)}
              </li>
            ))}
          </ol>
          <p className="note">{st.direct_link.note}</p>
        </section>

        <div className="two-col">
          <section>
            <h3>Supporting</h3>
            <p className="muted">Excludes the medication and the symptom themselves: the subject of a question can't support it.</p>
            <ItemList items={st.support} s={s} select={select} empty="The records searched contain no item that supports this question on its own." />
          </section>
          <section>
            <h3>Challenging</h3>
            <p className="muted">Every point on the challenge checklist is addressed, or recorded as unaddressed.</p>
            <ItemList items={st.challenge} s={s} select={select} empty="No challenging evidence was found in the records searched." />
          </section>
        </div>

        {st.unplaced.length > 0 && (
          <section>
            <h3>Not placed on either side</h3>
            <ItemList items={st.unplaced} s={s} select={select} empty="" />
          </section>
        )}

        <section>
          <h3>Summary to bring to your clinician</h3>
          <div className="rendered">{s.expected.rendering?.reference_text ?? ""}</div>
          {s.authority_inputs.some(a => a.status.includes('STUB')) && (
            <p className="stub-warning">The medical sources in this example are test stubs, not real citations. Real sources are attached by the retrieval layer.</p>
          )}
        </section>
      </article>
    </div>
  );
}

export function OpenItems({ s, select }: { s: Scenario; select: (id: string) => void }) {
  return (
    <div className="view">
      <header className="view-head">
        <h1>Open items</h1>
        <p className="lede">What PAi looked for and didn't find, and patterns worth asking about. "Not found" always means not found in the records you provided.</p>
      </header>
      <section>
        <h2 className="section-title">Looked for, not found</h2>
        <ul className="gaps">
          {s.expected.gaps.map(g => (
            <li key={g.id}>
              <p><strong>{g.sought.join(', ')}</strong></p>
              <p>{g.rendered}</p>
              <p className="muted">Searched {g.search_scope.join(', ')}; window {g.date_window[0]} to {g.date_window[1]}.</p>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 className="section-title">Worth asking about</h2>
        <ul className="gaps">
          {s.expected.flags.map(f => (
            <li key={f.id}>
              <p>{f.rendered.replace(/^[A-Z_]+:\s*/, '')}</p>
              <p className="muted">Based on <Cites c={f.cites} s={s} select={select} /></p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
