import type { Tracker } from '../contract/types';
import { LineChart } from '../components/Chart';
import { ReadingForms } from '../components/LogForms';

export function Vitals({ t, live = false, onLogged }: { t: Tracker; live?: boolean; onLogged?: () => void }) {
  const bpAm = t.vitals.bp.filter(r => r.slot === 'AM');
  const bpPm = t.vitals.bp.filter(r => r.slot === 'PM');
  const daily = t.vitals.daily;
  return (
    <div className="view">
      <header className="view-head">
        <h1>Vitals and devices</h1>
        <p className="lede">Charts show your readings as recorded. Blood pressure comes from your home cuff; the watch doesn't measure it.</p>
      </header>

      {live && <section><h2 className="section-title">Log a reading</h2><ReadingForms onLogged={() => onLogged?.()} /></section>}

      <section>
        <h2 className="section-title">Blood pressure, systolic</h2>
        <LineChart yLabel="Systolic blood pressure" height={220}
          marker={t.vitals.markers ? (t.vitals.markers[0] ?? undefined) : { x: t.vitals.lisinopril_start, label: 'Lisinopril started' }}
          refLines={[{ y: 140, label: '140' }, { y: 130, label: '130' }]}
          series={[
            { name: 'Morning', tone: 'primary', points: bpAm.map(r => ({ x: r.date, y: r.systolic })) },
            { name: 'Evening', tone: 'secondary', points: bpPm.map(r => ({ x: r.date, y: r.systolic })) },
          ]} />
        <p className="muted">{t.vitals.bp.length} readings in the last 90 days{t.vitals.bp.some(r => r.standing === 'FIRST_PERSON') ? ', including ones logged by you' : ''}. Technique: seated, 5 minutes rest, arm at heart level.</p>
        {t.raises_bp.map(r => <p key={r.item} className="note">{r.item}, logged {r.logged}. {r.text}</p>)}
      </section>

      <section>
        <h2 className="section-title">Resting heart rate</h2>
        <LineChart yLabel="Resting heart rate" height={170}
          series={[{ name: 'Resting HR (watch)', tone: 'primary', points: daily.filter(d => d.resting_hr !== null).map(d => ({ x: d.date, y: d.resting_hr as number })) }]} />
      </section>

      <section>
        <h2 className="section-title">Steps</h2>
        <LineChart yLabel="Steps" height={150}
          series={[{ name: 'Steps (main device each day)', tone: 'primary', points: daily.filter(d => d.steps !== null).map(d => ({ x: d.date, y: d.steps as number })) }]} />
      </section>

      <div className="two-col">
        <section>
          <h2 className="section-title">Sleep</h2>
          <LineChart yLabel="Hours of sleep" height={150}
            series={[{ name: 'Hours (watch)', tone: 'secondary', points: daily.filter(d => d.sleep_hours !== null).map(d => ({ x: d.date, y: d.sleep_hours as number })) }]} />
        </section>
        <section>
          <h2 className="section-title">Fasting glucose</h2>
          <LineChart yLabel="Fasting glucose" height={150} refLines={[{ y: 126, label: '126' }]}
            series={[{ name: 'mg/dL (home meter)', tone: 'primary', points: t.vitals.glucose.map(g => ({ x: g.date, y: g.fasting_mg_dl })) }]} />
        </section>
      </div>
      <p className="muted">Reference lines mark the thresholds the check-in rules use. They are not diagnoses.</p>
    </div>
  );
}
