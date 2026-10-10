import { useState } from 'react';
import { api } from '../contract/app';
import type { Tracker } from '../contract/types';

export function Labs({ t, go }: { t: Tracker; go?: (r: string) => void }) {
  const l = t.labs;
  type Row = { analyte: string; value: string; unit: string; ref_low: string; ref_high: string; ref_text: string; flag: string };
  const blank = (): Row => ({ analyte: '', value: '', unit: '', ref_low: '', ref_high: '', ref_text: '', flag: '' });
  const [rows, setRows] = useState<Row[]>([blank()]);
  const [date, setDate] = useState(new Date().toLocaleDateString('en-CA'));
  const [panel, setPanel] = useState('');
  const [labName, setLabName] = useState('');
  const [note, setNote] = useState('');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [localResults, setLocalResults] = useState<Array<{ at: string; analyte: string; value?: number; value_text?: string; unit?: string; flag?: string; origin: string }>>([]);
  const update = (i: number, key: keyof Row, value: string) => setRows(old => old.map((r, n) => n === i ? { ...r, [key]: value } : r));
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      if (!date || !rows.length) throw new Error('Enter the collection date and at least one result.');
      for (const r of rows) {
        if (!r.analyte.trim() || !r.value.trim()) throw new Error('Each row needs an analyte and a value.');
        const numeric = Number(r.value);
        const result: { analyte: string; value?: number; value_text?: string; unit?: string; ref_low?: string; ref_high?: string; ref_text?: string; flag?: string } = { analyte: r.analyte, ...(Number.isFinite(numeric) && r.value.trim() !== '' ? { value: numeric } : { value_text: r.value }),
          unit: r.unit || undefined, ref_low: r.ref_low || undefined, ref_high: r.ref_high || undefined,
          ref_text: r.ref_text || undefined, flag: r.flag || undefined };
        await api.post('log', { type: 'LAB', at: date, panel, lab_name: labName, note, results: [result] });
        setLocalResults(old => [...old.filter(v => !(v.at === date && v.analyte.toLowerCase() === r.analyte.toLowerCase())),
          { at: date, analyte: r.analyte, ...(typeof result.value === 'number' ? { value: result.value } : { value_text: r.value }), unit: r.unit, flag: r.flag, origin: 'USER_TYPED' }]);
      }
      setRows([blank()]); setMessage('Saved to encrypted records. Reload the view to refresh the tracker.');
    } catch (err) { setMessage(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const typed = (l as typeof l & { typed_results?: typeof localResults }).typed_results || [];
  const shown = [...typed.filter(r => !localResults.some(v => v.at === r.at && v.analyte.toLowerCase() === r.analyte.toLowerCase())), ...localResults];
  return (
    <div className="view">
      <header className="view-head">
        <h1>Labs</h1>
        <p className="lede">
          {l.annual_status === 'OVERDUE'
            ? `This year's labs haven't been entered${l.last_entered === 'never' ? '' : ` (last: ${l.last_entered})`}. Everything still runs, but some monitoring and suggestions wait on them.`
            : 'This year\'s labs are entered.'}
        </p>
      </header>

      <section>
        <h2 className="section-title">Ask your clinician to include</h2>
        <table className="totals">
          <tbody>
            {l.ask_to_include.map(r => (
              <tr key={r.test}><th>{r.test}</th><td>{r.why}</td><td className="muted">{r.source}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="muted">Most US health plans cover a yearly preventive visit without cost-sharing.</p>
      </section>

      <section>
        <h2 className="section-title">What entering them turns on</h2>
        <ul className="plain-list">{l.unlocks.map(u => <li key={u}>{u}</li>)}</ul>
      </section>

      <section>
        <h2 className="section-title">Add results</h2>
        <div className="entry-methods">
          {l.entry_methods.filter(m => !m.includes('Type values') && !m.includes('Enter results')).map((m, i) => <button key={m} className={i === 0 ? 'btn' : 'btn-quiet'} onClick={() => go?.('ingest')}>{m}</button>)}
          <button className='btn' onClick={() => setOpen(v => !v)}>Enter results by hand</button>
        </div>
      </section>
      {open && <section className="logform">
        <h2 className="section-title">Enter results by hand</h2>
        <p className="muted">Transcribe what your paper report says. These values are marked “you typed this” and are not verified against a document.</p>
        <div className="row wrap">
          <label className="field"><span>Collection date</span><input type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
          <label className="field"><span>Panel (optional)</span><input value={panel} onChange={e => setPanel(e.target.value)} /></label>
          <label className="field"><span>Lab name (optional)</span><input value={labName} onChange={e => setLabName(e.target.value)} /></label>
        </div>
        <datalist id="lab-analytes">{['glucose','hemoglobin A1c','LDL cholesterol','HDL cholesterol','total cholesterol','creatinine','eGFR','potassium','sodium','TSH','triglycerides','hemoglobin','ferritin','vitamin B12','vitamin D'].map(a => <option value={a} key={a} />)}</datalist>
        {rows.map((r, i) => <div key={i} className="row wrap">
          <label className="field"><span>Analyte</span><input list="lab-analytes" value={r.analyte} onChange={e => update(i, 'analyte', e.target.value)} /></label>
          <label className="field sm"><span>Value / qualitative</span><input value={r.value} onChange={e => update(i, 'value', e.target.value)} placeholder="e.g. 98 or Negative" /></label>
          <label className="field sm"><span>Unit</span><input value={r.unit} onChange={e => update(i, 'unit', e.target.value)} placeholder="mg/dL" /></label>
          <label className="field sm"><span>Reference low</span><input inputMode="decimal" value={r.ref_low} onChange={e => update(i, 'ref_low', e.target.value)} /></label>
          <label className="field sm"><span>Reference high</span><input inputMode="decimal" value={r.ref_high} onChange={e => update(i, 'ref_high', e.target.value)} /></label>
          <label className="field sm"><span>Reference text</span><input value={r.ref_text} onChange={e => update(i, 'ref_text', e.target.value)} /></label>
          <label className="field sm"><span>Printed flag</span><select value={r.flag} onChange={e => update(i, 'flag', e.target.value)}>{['', 'H', 'L', 'A'].map(x => <option key={x} value={x}>{x || 'None'}</option>)}</select></label>
          {rows.length > 1 && <button className="btn-quiet" onClick={() => setRows(old => old.filter((_, n) => i !== n))}>Remove</button>}
        </div>)}
        <label className="field"><span>Note (optional)</span><input value={note} onChange={e => setNote(e.target.value)} /></label>
        <div className="row wrap"><button className="btn-quiet" onClick={() => setRows(old => [...old, blank()])}>Add row</button><button className="btn" disabled={busy} onClick={save}>Save results</button></div>
        {message && <p role="status">{message}</p>}
      </section>}
      {shown.length > 0 && <section><h2 className="section-title">Entered by hand</h2><table className="totals"><tbody>{shown.map((r, i) => <tr key={i}><th>{r.analyte}</th><td>{r.value ?? r.value_text} {r.unit || ''}</td><td>{r.at}</td><td>{r.flag || ''}</td><td><span className="badge">you typed this</span></td></tr>)}</tbody></table></section>}
    </div>
  );
}
