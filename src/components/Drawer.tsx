import type { FloatFact, Scenario } from '../contract/types';
import { CERTAINTY_LABEL, describeDate, KIND_LABEL, SPEAKER_LABEL, TEMPORALITY_LABEL } from '../contract/format';
import { LockPair, StandingTag } from './Status';

/** Provenance view for one Float: the exact span in its source, and every axis. */
export function FloatDrawer({ fact, scenario, onClose }: { fact: FloatFact; scenario: Scenario; onClose: () => void }) {
  const doc = scenario.documents[fact.provenance.document_id];
  const { char_start: a, char_end: b } = fact.provenance;
  const sameRoot = scenario.expected.floats.filter(f => f.root === fact.root && f.id !== fact.id);
  return (
    <aside className="drawer" aria-label={`Details for ${fact.id}`}>
      <header className="drawer-head">
        <div>
          <p className="drawer-id">{fact.id}</p>
          <h2>{fact.concept}</h2>
        </div>
        <button className="btn-quiet" onClick={onClose}>Close</button>
      </header>

      <section>
        <LockPair lock={fact.provenance_lock} status={fact.content_status} />
        <StandingTag standing={fact.standing} />
      </section>

      <section>
        <h3>In the source</h3>
        <p className="drawer-docmeta">{doc?.title}, {doc?.date}, page {fact.provenance.page}, characters {a}–{b}</p>
        {doc && (
          <blockquote className="source">
            {doc.text.slice(0, a)}<mark>{doc.text.slice(a, b)}</mark>{doc.text.slice(b)}
          </blockquote>
        )}
        <p className="hash">Document SHA-256 {fact.provenance.document_sha256.slice(0, 16)}…</p>
      </section>

      <section>
        <h3>How it was read</h3>
        <dl className="axes">
          <dt>Kind</dt><dd>{KIND_LABEL[fact.kind]}</dd>
          <dt>Said by</dt><dd>{SPEAKER_LABEL[fact.speaker]}{fact.provenance.relay && fact.provenance.relay !== 'DIRECT' ? ` (${fact.provenance.relay.toLowerCase()})` : ''}</dd>
          <dt>Polarity</dt><dd>{fact.polarity === 'NEGATED' ? 'Negated (stated as absent)' : 'Affirmed'}</dd>
          <dt>Certainty</dt><dd>{CERTAINTY_LABEL[fact.certainty]}</dd>
          <dt>Time</dt><dd>{TEMPORALITY_LABEL[fact.temporality]}</dd>
          <dt>About</dt><dd>{fact.experiencer === 'PATIENT' ? 'The patient' : fact.experiencer === 'FAMILY' ? 'A family member' : 'Someone else'}</dd>
          <dt>Date</dt><dd>{describeDate(fact.event_date)}</dd>
          {fact.section && <><dt>Section</dt><dd>{fact.section}</dd></>}
          {fact.medication && <><dt>Medication</dt><dd>{fact.medication.drug}, recorded as {fact.medication.med_status.replace(/_/g, ' ').toLowerCase()}</dd></>}
          {fact.action_state && <><dt>Action</dt><dd>{fact.action_state.toLowerCase()}</dd></>}
          {fact.value && <><dt>Value</dt><dd>{fact.value.numeric} {fact.value.unit}{fact.value.reference_low != null ? `, printed range ${fact.value.reference_low}–${fact.value.reference_high}` : ''}{fact.value.performing_lab ? '' : '; lab not stated'}</dd></>}
          {fact.range_comparison && <><dt>Range check</dt><dd>{fact.range_comparison.result.toLowerCase()} the printed range (computed by PAi, not flagged by the lab)</dd></>}
          <dt>Source root</dt><dd>{fact.root}{sameRoot.length ? ` — shared with ${sameRoot.map(f => f.id).join(', ')} (copied text counts once)` : ''}</dd>
        </dl>
      </section>

      {fact.note && <section><h3>Note</h3><p>{fact.note}</p></section>}
    </aside>
  );
}
