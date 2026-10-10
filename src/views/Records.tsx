import { useState } from 'react';
import type { Scenario } from '../contract/types';
import { describeDate, KIND_LABEL, SPEAKER_LABEL } from '../contract/format';
import { LockPair } from '../components/Status';

export function Records({ s, select, selected }: { s: Scenario; select: (id: string) => void; selected: string | null }) {
  const docs = Object.entries(s.documents);
  const [doc, setDoc] = useState<string>('ALL');
  const floats = s.expected.floats.filter(f => doc === 'ALL' || f.provenance.document_id === doc);
  return (
    <div className="view">
      <header className="view-head">
        <h1>Records</h1>
        <p className="lede">Every statement PAi read from your documents, with exactly where it came from. A statement here records that the document said it, not that it's true.</p>
        <p className="legend-line">
          <span><span className="lock lock-verified_span"><span className="lock-dot" /></span> source text matches</span>
          <span><span className="bracket bracket-unverified"><span className="bracket-mark">[{'{'} {'}'}]</span></span> not independently corroborated</span>
          <span><span className="bracket bracket-explained"><span className="bracket-mark">{'{'}[ ]{'}'}</span></span> independently documented</span>
          <span>Select a row for the full source.</span>
        </p>
      </header>

      <div className="doc-tabs" role="tablist">
        <button role="tab" aria-selected={doc === 'ALL'} className={doc === 'ALL' ? 'tab on' : 'tab'} onClick={() => setDoc('ALL')}>All documents</button>
        {docs.map(([id, d]) => (
          <button key={id} role="tab" aria-selected={doc === id} className={doc === id ? 'tab on' : 'tab'} onClick={() => setDoc(id)}>
            {d.title} <span className="tab-date">{d.date}</span>
          </button>
        ))}
      </div>

      <table className="facts">
        <thead>
          <tr><th className="col-stmt">Statement</th><th>Kind and source</th><th>Date</th><th>Status</th></tr>
        </thead>
        <tbody>
          {floats.map(f => (
            <tr key={f.id} className={selected === f.id ? 'sel' : ''} onClick={() => select(f.id)} tabIndex={0}
              onKeyDown={e => { if (e.key === 'Enter') select(f.id); }}>
              <td>
                <span className="quote">{f.original_text}</span>
                <span className="concept">{f.concept}{f.polarity === 'NEGATED' ? ' — stated as absent' : ''}{f.certainty !== 'DEFINITE' ? ` — ${f.certainty === 'HEDGED' ? 'hedged' : 'under consideration'}` : ''}</span>
              </td>
              <td>{KIND_LABEL[f.kind]}<span className="concept">{SPEAKER_LABEL[f.speaker]}</span></td>
              <td>{describeDate(f.event_date)}</td>
              <td><LockPair compact lock={f.provenance_lock} status={f.content_status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
