import type { Settings, Tracker } from '../contract/types';
import { useState } from 'react';
import { Bracket, StandingTag } from '../components/Status';
import { MedicationForm, logEntry } from '../components/LogForms';

export function Medications({ t, settings, live = false, onLogged }: { t: Tracker; settings: Settings; live?: boolean; onLogged?: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const done = () => { setEditing(null); onLogged?.(); };
  const logDose = async (id: string, name: string) => {
    try { await logEntry({ type: 'DOSE', med_id: id, taken: 'YES' }); setMsg(`Dose of ${name} logged just now.`); onLogged?.(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="view">
      <header className="view-head">
        <h1>Medications</h1>
        <p className="lede">Your logs are the record. Sensors can add confirmation; a missing sensor event never counts against a dose you logged.</p>
      </header>

      {live && (editing === 'NEW'
        ? <MedicationForm onSaved={done} onCancel={() => setEditing(null)} />
        : <button className="btn" onClick={() => setEditing('NEW')}>Add a medication</button>)}
      {msg && <p className="ok small">{msg}</p>}
      {t.medications.length === 0 && <p className="empty">No medications yet.</p>}

      <ul className="meds">
        {t.medications.map(m => {
          const status = m.confirmation ? (settings.device_dose_confirmation ? m.confirmation.status_on : m.confirmation.status_off) : null;
          return (
            <li key={m.id} className="med">
              <div className="med-head">
                <h2>{m.name}</h2>
                <span className="med-dose">{m.dose}</span>
                <StandingTag standing={m.origin === 'RECORD' ? 'CLINICAL' : 'FIRST_PERSON'} />
                {live && m.origin !== 'RECORD' && <span className="row">
                  <button className="btn-quiet" onClick={() => logDose(m.id, m.name)}>I took a dose now</button>
                  <button className="link" onClick={() => setEditing(m.id)}>Edit</button></span>}
                {live && m.origin === 'RECORD' && <button className="link" onClick={() => setEditing(m.id)}>Track this one</button>}
              </div>
              {editing === m.id && <MedicationForm initial={{ med_id: m.origin === 'RECORD' ? undefined : m.id, name: m.name, dose: m.dose, schedule: m.schedule ?? [], start: m.start ?? '', targets: m.targets ?? [] }} onSaved={done} onCancel={() => setEditing(null)} />}
              <dl className="axes">
                <dt>Started</dt><dd>{m.start ?? 'Not known'} <span className="muted">({m.start_basis})</span></dd>
                <dt>Doses</dt><dd>{m.adherence}</dd>
                {m.sensor && <><dt>Sensor</dt><dd>{m.sensor}</dd></>}
                <dt>Missed-dose check</dt>
                <dd>
                  <span className={`verify verify-${m.verification.state.toLowerCase()}`}>
                    {m.verification.state === 'QUALIFIED' ? 'Active' : m.verification.state === 'LEARNING' ? 'Learning' : 'Schedule only'}
                  </span>{' '}{m.verification.text}
                </dd>
                {m.confirmation && status && (
                  <>
                    <dt>Dose events</dt>
                    <dd>
                      <Bracket status={status} /> {m.confirmation.label}
                      {m.confirmation.sensor_class === 'DIRECT_DELIVERY' && settings.device_dose_confirmation && <span className="muted"> Confirmed by device (your setting).</span>}
                    </dd>
                  </>
                )}
              </dl>
            </li>
          );
        })}
      </ul>

      {t.change_windows.map(w => (
        <section key={w.id} className="window">
          <h2 className="section-title">Before and after: {w.anchor}</h2>
          <p className="muted">Measure: {w.measure}. Chosen by {w.target_set_by}.</p>
          <div className="window-grid">
            <div><p className="window-label">{w.pre.days} days before</p><p className="window-num">{w.pre.mean_sys.toFixed(0)}/{w.pre.mean_dia.toFixed(0)}</p><p className="muted">{w.pre.readings} readings</p></div>
            <div><p className="window-label">{w.post.days} days after</p><p className="window-num">{w.post.mean_sys.toFixed(0)}/{w.post.mean_dia.toFixed(0)}</p><p className="muted">{w.post.readings} readings</p></div>
            <div><p className="window-label">Your usual day-to-day spread</p><p className="window-num">±{w.pre.daily_spread_sys}</p><p className="muted">systolic, before the start</p></div>
          </div>
          <p>The difference is {w.difference_vs_spread === 'LARGER' ? 'larger than' : w.difference_vs_spread === 'SMALLER' ? 'smaller than' : 'about the same as'} your usual day-to-day variation. {w.adherence}.</p>
          <h3>Other explanations to keep in view</h3>
          <ul className="plain-list">{w.challenge.map(c => <li key={c.item}><strong>{c.item}.</strong> {c.text}</li>)}</ul>
          <p className="note">This shows what changed, not whether the medication works. Dose questions go to your clinician.</p>
        </section>
      ))}
    </div>
  );
}
