import { useEffect, useState } from 'react';
import { api, type BillingField, type BillingProfile, type BillingRecord, type BillingRow, type BillingState, type DocDetail } from '../contract/app';

/** Billing reading for one document. Renders what the engine read; computes nothing. */

const ASSERTS: Record<string, string> = {
  submitted: 'charges as submitted on a claim — not proof the service was performed or paid',
  estimated: 'expected charges — not services received',
  billed: 'a request for payment from the provider',
  adjudicated: 'the payer’s decision on a claim — not a bill',
  'paid-as-reported': 'a payment as the document reports it — not a bank record',
  'bank-recorded': 'a bank’s record of money moving',
};

const FIELD: Record<string, string> = {
  patient: 'Patient', guarantor: 'Guarantor', subscriber: 'Insured', subscriber_id: 'Member / insured ID', group: 'Group',
  claim_id: 'Claim / account no.', medical_record: 'Medical record no.', provider: 'Provider', rendering_provider: 'Rendering provider ID',
  payer: 'Payer', tax_id: 'Tax ID', statement_date: 'Statement date', due_date: 'Due date', statement_period: 'Statement period',
  bill_type: 'Type of bill', diagnosis: 'Diagnosis (as printed)', total: 'Total', amount_paid: 'Amount paid', balance: 'Balance due',
  appeal: 'Appeal wording', appeal_deadline: 'Appeal deadline', service_date: 'Service date', posting_date: 'Posted',
  description: 'Description', service_code: 'Code', modifier: 'Modifier', revenue_code: 'Rev. code', units: 'Units',
  billed: 'Billed', allowed: 'Allowed', approved: 'Approved', adjustments: 'Adjustment', deductible: 'Deductible', copay: 'Copay',
  coinsurance: 'Coinsurance', paid: 'Paid', medicare_paid: 'Medicare paid', patient_amount: 'Patient amount',
  maximum_patient_amount: 'Most you may be billed', payments: 'Payments', insurance_pending: 'Insurance pending',
  noncovered: 'Non-covered', place_of_service: 'Place', diagnosis_pointer: 'Dx pointer', remarks: 'Notes',
  amount_unqualified: 'Amount (unlabeled)', unlabeled_column: 'Unlabeled column', unmapped: 'Unplaced text',
  resource_id: 'Resource id', identifier: 'Identifier', status: 'Status', use: 'Use', outcome: 'Outcome', created: 'Created',
  disposition: 'Disposition', claim_type: 'Claim type', insurer: 'Insurer', issuer: 'Issuer', recipient: 'Recipient',
  subject: 'Subject', request: 'Request', response: 'Response', claim: 'Claim', claim_response: 'Claim response',
  payment_type: 'Payment type', payment_date: 'Payment date', payment_identifier: 'Payment id', payment_issuer: 'Payment issuer',
  payment_adjustment: 'Payment adjustment', total_net: 'Total net', total_gross: 'Total gross', payment_terms: 'Payment terms',
  sequence: 'Seq.', item_sequence: 'Item seq.', net: 'Net', unit_price: 'Unit price', adjudication_amount: 'Adjudication',
  adjudication_value: 'Adjudication value', adjudication_reason: 'Reason', price_component_amount: 'Price component',
  price_component_factor: 'Factor', coverage: 'Coverage', diagnosis_sequence: 'Dx seq.', allocation_type: 'Type',
  billable_period_start: 'Billable from', billable_period_end: 'Billable to', service_period_start: 'From', service_period_end: 'To',
};
// Amounts whose meaning is their own category label: shown with it, never paired by position.
const LABELED = new Set(['adjudication_amount', 'adjudication_value', 'price_component_amount', 'price_component_factor', 'total']);
const HIDDEN = new Set(['adjudication_category', 'price_component_type']);

const ISSUE: Record<string, string> = {
  TYPE_MISMATCH: 'doesn’t read as the column’s type', AMBIGUOUS_COLUMN_LABEL: 'column label doesn’t say what amount this is',
  UNRECOGNIZED_COLUMN: 'column label not recognized', UNMAPPED_CELL: 'not under any column', AMBIGUOUS_CELL_POSITION: 'sits between columns',
  MULTIPLE_VALUES_IN_COLUMN: 'more than one value in this column', SOURCE_CONFLICT: 'printed with different values — choose one',
  TWO_DIGIT_YEAR: 'two-digit year', CENTS_IN_SEPARATE_BOX: 'dollars and cents in separate boxes',
  LOW_OCR_CONFIDENCE: 'OCR unsure', REPEATED_ROW: 'printed twice', TOTAL_DIFFERS_FROM_LINES: 'differs from the lines',
  CATEGORY_MISSING: 'no category given', CURRENCY_NOT_STATED: 'currency not stated', WRAPPED_CONTINUATION: 'continued line',
  INVALID_DATE: 'not a real date', RANGE_END_BEFORE_START: 'range ends before it starts', SIGN_AMBIGUOUS: 'sign unclear',
};
const issueText = (i: string) => ISSUE[i] ?? (i.startsWith('MISSING_') ? `no ${(FIELD[i.slice(8).toLowerCase()] ?? i.slice(8).toLowerCase()).toLowerCase()}` : i.toLowerCase().replace(/_/g, ' '));
const name = (f: string) => FIELD[f] ?? f.replace(/_/g, ' ');

function Value({ f, show }: { f: BillingField; show: (p: number) => void }) {
  const where = f.locator.json_pointer ?? (f.locator.page ? `page ${f.locator.page}` : '');
  const tip = [f.normalized_value !== null && f.normalized_value !== f.raw_value ? `read as ${f.normalized_value}` : null,
    f.label ? `label: ${f.label}` : null, where, f.sign_basis ? `negative by ${f.sign_basis}` : null].filter(Boolean).join(' · ');
  return (
    <span className={f.issues.some(i => i !== 'WRAPPED_CONTINUATION' && i !== 'TWO_DIGIT_YEAR' && i !== 'CENTS_IN_SEPARATE_BOX') ? 'bill-val warn' : 'bill-val'} title={tip}>
      {LABELED.has(f.field) && f.locator.json_pointer && <span className="bill-cat">{f.label ?? 'no category'}</span>}
      {f.locator.page ? <button className="link bill-link" onClick={() => show(f.locator.page!)} title={`Show page ${f.locator.page}`}>{f.raw_value}</button> : f.raw_value}
      {f.issues.length > 0 && <span className="concept">{f.issues.map(issueText).join(' · ')}</span>}
    </span>
  );
}

function Rows({ rows, totals, show }: { rows: BillingRow[]; totals: BillingRow | null; show: (p: number) => void }) {
  const cols: string[] = [];
  for (const r of [...rows, ...(totals ? [totals] : [])]) for (const f of r.fields) if (!cols.includes(f.field) && !HIDDEN.has(f.field)) cols.push(f.field);
  const cell = (r: BillingRow, c: string) => r.fields.filter(f => f.field === c);
  return (
    <div className="bill-scroll">
      <table className="bill-table">
        <thead><tr><th>Row</th>{cols.map(c => <th key={c}>{name(c)}</th>)}<th>Notes</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.row_id} className={r.issues.length ? 'flag' : ''}>
              <td className="muted small">{r.row_id}</td>
              {cols.map(c => <td key={c}>{cell(r, c).map((f, k) => <Value key={k} f={f} show={show} />)}</td>)}
              <td className="small warn">{r.issues.map(issueText).join(' · ')}</td>
            </tr>
          ))}
          {totals && (
            <tr className="bill-total">
              <td className="small">Printed total</td>
              {cols.map(c => <td key={c}>{cell(totals, c).map((f, k) => <Value key={k} f={f} show={show} />)}</td>)}
              <td className="small warn">{totals.issues.map(issueText).join(' · ')}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Record({ r, show }: { r: BillingRecord; show: (p: number) => void }) {
  return (
    <div className="bill-record">
      <h3 className="sub">{r.title}</h3>
      <p className="small">This document asserts <strong>{r.assertion_kind}</strong>: {ASSERTS[r.assertion_kind]}.</p>
      <p className="muted small">
        {r.confirmation.basis === 'PERSON' ? `Form type confirmed by ${r.confirmation.by ?? 'you'}${r.confirmation.at ? ` on ${r.confirmation.at.slice(0, 10)}` : ''}.` : `Read from the file itself (${r.confirmation.detail}).`}
        {r.adapter_status.startsWith('UNVERIFIED') && ' This reader hasn’t yet been checked against a real document of this kind.'}
        {r.fhir_version && ` FHIR version: ${r.fhir_version}.`}
      </p>
      {r.issues.length > 0 && (
        <ul className="plain-list small bill-issues">{r.issues.map((i, k) => <li key={k} className="warn">{i.message}</li>)}</ul>
      )}
      {r.header.length > 0 && (
        <table className="bill-table bill-head">
          <tbody>{r.header.map((f, k) => (
            <tr key={k}><th>{name(f.field)}{f.label && !LABELED.has(f.field) && f.label.toLowerCase() !== name(f.field).toLowerCase() && f.label.toLowerCase() !== f.field ? <span className="concept">{f.locator.json_pointer ? f.label : `printed as “${f.label}”`}</span> : null}</th><td><Value f={f} show={show} /></td></tr>
          ))}</tbody>
        </table>
      )}
      {r.tables.map(t => (
        <div key={t.table_id}>
          {t.columns.some(c => c.field === 'unlabeled_column' || c.field === 'amount_unqualified') && (
            <p className="muted small">Columns read: {t.columns.map(c => `${c.label} → ${name(c.field)}`).join(' · ')}</p>
          )}
          <Rows rows={t.rows} totals={t.totals} show={show} />
        </div>
      ))}
      {r.not_found && r.not_found.length > 0 && <p className="muted small">Not found on this document (left unknown): {r.not_found.map(name).join(', ')}.</p>}
      {r.notes.filter(Boolean).map((n, k) => <p key={k} className="muted small">{n}</p>)}
    </div>
  );
}

export function BillingPanel({ d, onDoc, show }: { d: DocDetail; onDoc: (d: DocDetail) => void; show: (p: number) => void }) {
  const b: BillingState | undefined = d.billing;
  const [profiles, setProfiles] = useState<BillingProfile[]>([]);
  const [pick, setPick] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<{ profiles: BillingProfile[] }>('billing/profiles').then(x => setProfiles(x.profiles)).catch(() => undefined); }, []);
  if (!b) return null;
  const paper = profiles.filter(p => p.confirm);
  const confirm = async (profile_id: string | null) => {
    setBusy(true); setErr(null);
    try { onDoc(await api.post<DocDetail>('billing/confirm', { doc_id: d.doc_id, profile_id })); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const recs = b.extraction?.records ?? [];
  const picker = (
    <div className="row">
      <select value={pick} onChange={e => setPick(e.target.value)} aria-label="Billing form type">
        <option value="">Choose the form type…</option>
        {paper.map(p => <option key={p.profile_id} value={p.profile_id}>{p.title}</option>)}
      </select>
      <button className="btn-quiet" disabled={!pick || busy} onClick={() => confirm(pick)}>Read as this</button>
    </div>
  );

  return (
    <section className="billing">
      <h3 className="sub">Billing</h3>
      {err && <p className="fatal-inline" role="alert">{err}</p>}
      {b.fhir.length === 0 && !b.confirmed && (
        b.candidates.length > 0 ? (
          <div className="callout">
            <p>This looks like {b.candidates.map(c => c.title).join(' or ')}. Confirm the form type to read its lines — the label on the page is a hint, not proof.</p>
            <div className="row">
              {b.candidates.filter(c => c.supported).map(c => (
                <button key={c.profile_id} className="btn" disabled={busy} onClick={() => confirm(c.profile_id)}>Yes, read as {c.title}</button>
              ))}
            </div>
            {b.candidates.some(c => !c.supported) && <p className="muted small">No reader yet for: {b.candidates.filter(c => !c.supported).map(c => c.title).join(', ')}.</p>}
            <details><summary className="small">It’s a different form</summary>{picker}</details>
          </div>
        ) : (
          <details><summary className="small">Is this a bill, claim form, EOB or Medicare notice?</summary>{picker}</details>
        )
      )}
      {b.form_fields_filled > 0 && !b.confirmed && <p className="small warn">This PDF keeps {b.form_fields_filled} entries in fillable fields, which aren’t part of its printed text. Print it to PDF and import that copy.</p>}
      {b.confirmed && (
        <p className="muted small">Read as {paper.find(p => p.profile_id === b.confirmed!.profile_id)?.title ?? b.confirmed.profile_id}.{' '}
          <button className="link" disabled={busy} onClick={() => confirm(null)}>Not this form</button></p>
      )}
      {b.extraction?.read_notes && b.extraction.read_notes.length > 0 && <p className="muted small">{b.extraction.read_notes.join(' · ')}</p>}
      {recs.map((r, k) => <Record key={k} r={r} show={show} />)}
      {recs.length > 0 && <p className="note">Every value is shown as printed, with where it came from. Nothing is inferred or matched to other documents; a printed total is only compared with its lines to flag a difference, and neither is changed.</p>}
    </section>
  );
}
