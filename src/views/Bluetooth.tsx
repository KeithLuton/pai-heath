import { useEffect, useState } from 'react';
import { api, waitJob, type Job } from '../contract/app';

interface Status { available: boolean; install: string; note: string }
interface Found { name: string | null; address: string; service_uuids: string[]; rssi: number; possible_health_device: boolean }
interface Batch {
  id: string; device_name: string; saved_at: string; samples: number; packets: number; rejected: number;
  by_measure: Record<string, number>; recent: { measure: string; value: number; unit: string; start: string; time_basis: string }[];
}

const LABEL: Record<string, string> = {
  heart_rate: 'Heart rate', bp_systolic: 'Systolic', bp_diastolic: 'Diastolic', bp_mean: 'Mean arterial',
  body_temperature: 'Temperature', body_mass: 'Weight', spo2: 'SpO₂',
};

/** Standard Bluetooth LE health devices → the encrypted vault, as unverified device readings. */
export function BluetoothPanel() {
  const [st, setSt] = useState<Status | null>(null);
  const [found, setFound] = useState<Found[] | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(60);

  const refresh = () => api.get<{ batches: Batch[] }>('devices/list').then(r => setBatches(r.batches)).catch(() => undefined);
  useEffect(() => { api.get<Status>('bluetooth/status').then(setSt).catch(e => setErr(String(e))); refresh(); }, []);
  const busy = job && (job.state === 'QUEUED' || job.state === 'RUNNING');

  const scan = async () => {
    setErr(null); setMsg(null); setFound(null);
    try {
      const { job: id } = await api.post<{ job: string }>('bluetooth/scan', { seconds: 10 });
      const j = await waitJob(id, setJob);
      if (j.state === 'FAILED') setErr(j.error); else setFound((j.result as { devices: Found[] }).devices);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const listen = async (d: Found) => {
    setErr(null); setMsg(null);
    try {
      const { job: id } = await api.post<{ job: string }>('bluetooth/collect', { address: d.address, seconds });
      const j = await waitJob(id, setJob, 1000);
      if (j.state === 'FAILED') { setErr(j.error); return; }
      const r = j.result as { saved: string | null; packets: number; samples: number; rejected: number; note?: string };
      setMsg(r.saved ? `Saved ${r.samples} reading${r.samples === 1 ? '' : 's'} from ${d.name ?? d.address} to your vault${r.rejected ? ` (${r.rejected} packet${r.rejected > 1 ? 's' : ''} couldn’t be read and were kept raw)` : ''}.` : r.note ?? 'Nothing arrived.');
      refresh();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <section className="ble">
      <div className="row-between">
        <h2 className="section-title">Bluetooth devices</h2>
        {st?.available && <div className="row">
          <label className="small muted">Listen for <select value={seconds} onChange={e => setSeconds(Number(e.target.value))}>
            {[30, 60, 120, 300].map(s => <option key={s} value={s}>{s} s</option>)}</select></label>
          <button className="btn-quiet" onClick={scan} disabled={!!busy}>{busy && job?.kind === 'BLE_SCAN' ? 'Scanning…' : 'Scan'}</button>
        </div>}
      </div>
      {st && <p className="muted small">{st.note}</p>}
      {st && !st.available && (
        <div className="callout"><p>Bluetooth needs one package. Install it once in PowerShell, then reopen PAi Health:</p><pre className="cmd">{st.install}</pre></div>
      )}
      {busy && <p className="muted">{job?.message}</p>}
      {found && (found.length === 0 ? <p className="muted">No Bluetooth devices found. Wake the device or put it in pairing mode and scan again.</p> : (
        <ul>
          {[...found].sort((a, b) => Number(b.possible_health_device) - Number(a.possible_health_device) || b.rssi - a.rssi).map(d => (
            <li key={d.address}>
              <span className={d.possible_health_device ? 'health' : ''}>{d.name ?? 'Unnamed device'} <span className="muted small">{d.address}{d.possible_health_device ? ' · health device' : ''}</span></span>
              <button className="btn-quiet" disabled={!!busy} onClick={() => listen(d)}>Listen</button>
            </li>
          ))}
        </ul>
      ))}
      {msg && <p className="ok">{msg}</p>}
      {err && <p className="fatal-inline" role="alert">{err}</p>}
      {batches.length > 0 && (
        <>
          <h3 className="sub">Saved readings</h3>
          <ul>
            {batches.map(b => (
              <li key={b.id}>
                <span><strong>{b.device_name}</strong> <span className="muted small">{b.saved_at.replace('T', ' ').replace('+00:00', ' UTC')}</span>
                  <span className="readings"><br />{b.recent.map(r => `${LABEL[r.measure] ?? r.measure} ${r.value} ${r.unit}${r.time_basis === 'RECEIVED_AT' ? '' : ' (device time)'}`).join(' · ')}</span></span>
                <span className="muted small">{b.samples} readings · unverified device data</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
