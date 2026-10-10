import { useState } from 'react';
import type { Tracker } from '../contract/types';
import { MealForm } from '../components/LogForms';

const STATE_TEXT = { WITHIN: 'Within target', BELOW: 'Below target', ABOVE: 'Above target', HELD: 'Target held', INFO: '' } as const;

export function Nutrition({ t, live = false, onLogged }: { t: Tracker; live?: boolean; onLogged?: () => void }) {
  const n = t.nutrition as any;
  const [hideCalories, setHide] = useState(false);
  const [logging, setLogging] = useState(false);

  return (
    <div className="view">
      <header className="view-head">
        <h1>Nutrition</h1>
        <p className="lede">Portions are estimates, so totals are shown as ranges.</p>
      </header>

      <section>
        <div className="row-between">
          <h2 className="section-title">Today's meals</h2>
          {live && !logging && (
            <button className="btn" onClick={() => setLogging(true)}>Log a meal</button>
          )}
        </div>
        {logging && (
          <MealForm
            onSaved={() => { setLogging(false); onLogged?.(); }}
            onCancel={() => setLogging(false)}
          />
        )}
        <ul className="meals">
          {n.meals.map((m: any, idx: number) => (
            <li key={`${m.time}-${idx}`}>
              <span className="meal-time">{m.time}</span>
              <span>{m.label}</span>
              <span className="muted">{m.method}</span>
            </li>
          ))}
        </ul>
        {n.meals.length === 0 && !logging && <p className="empty">No meals logged today.</p>}
        {!live && <p className="muted small">Unlock a user to log meals.</p>}
      </section>

      <section>
        <div className="row-between">
          <h2 className="section-title">Today's totals</h2>
          <label className="toggle-inline">
            <input type="checkbox" checked={hideCalories} onChange={e => setHide(e.target.checked)} /> Hide calories
          </label>
        </div>
        {n.source && <p className="source-line">Source: {n.source}</p>}
        {n.totals.length === 0 ? (
          <p className="empty">No meal totals for today yet. Log a meal above to track nutrient totals.</p>
        ) : (
          <table className="totals">
            <tbody>
              {n.totals.filter((r: any) => !(hideCalories && r.nutrient === 'Calories')).map((r: any) => (
                <tr key={r.nutrient}>
                  <th>{r.nutrient}</th>
                  <td className="num">{r.range}</td>
                  <td>{r.target}</td>
                  <td><span className={`state state-${r.state.toLowerCase()}`}>{STATE_TEXT[r.state as keyof typeof STATE_TEXT] || r.state}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="section-title">Checked against your medications</h2>
        <ul className="plain-list">
          {n.interaction_checks.map((c: any) => (
            <li key={c.pair}><strong>{c.pair}.</strong> {c.text}</li>
          ))}
        </ul>
      </section>

      <p className="note">{n.excluded_note}</p>
    </div>
  );
}