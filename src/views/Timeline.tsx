import type { Scenario } from '../contract/types';
import { describeDate } from '../contract/format';

const RES_TEXT: Record<string, string> = {
  EXACT: 'Exact interval', RANGE: 'Interval range', ORDER_ONLY: 'Order known, interval unknown', INDETERMINATE: 'Order unknown',
};

export function Timeline({ s, select }: { s: Scenario; select: (id: string) => void }) {
  const byDoc = Object.entries(s.documents).sort((a, b) => a[1].date.localeCompare(b[1].date));
  const floatById = Object.fromEntries(s.expected.floats.map(f => [f.id, f]));
  return (
    <div className="view">
      <header className="view-head">
        <h1>Timeline</h1>
        <p className="lede">Events in the order the records support. Where a date is approximate or only "on or before" a report, it's shown that way; PAi never sharpens a date.</p>
      </header>

      <ol className="timeline">
        {byDoc.map(([id, d]) => (
          <li key={id}>
            <p className="tl-date">{d.date}</p>
            <div className="tl-body">
              <h2>{d.title}</h2>
              <ul>
                {s.expected.floats.filter(f => f.provenance.document_id === id).map(f => (
                  <li key={f.id}>
                    <button className="link" onClick={() => select(f.id)}>{f.concept}</button>
                    {f.polarity === 'NEGATED' && <span className="muted"> (stated as absent)</span>}
                    <span className="muted"> — {describeDate(f.event_date)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </li>
        ))}
      </ol>

      <section>
        <h2 className="section-title">Relationships between statements</h2>
        <p className="muted">Sequence and quantity only. A relationship in time is never a cause.</p>
        <ul className="relations">
          {s.expected.mixes.map(m => (
            <li key={m.id}>
              <p className="rel-head">{m.endpoints.map(e => floatById[e]?.concept ?? e).join(' → ')}</p>
              <p>{m.rendered ?? m.relation.replace(/_/g, ' ').toLowerCase()}</p>
              {m.temporal && <p className="muted">{RES_TEXT[m.temporal.temporal_resolution]}{m.temporal.order_basis ? `, based on ${m.temporal.order_basis === 'REPORT_SEQUENCE' ? 'the order of the reports' : m.temporal.order_basis.toLowerCase()}` : ''}.</p>}
              {m.comparability && <p className="muted">Comparability: {m.comparability.class.toLowerCase()} — {m.comparability.reasons.join('; ')}.</p>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
