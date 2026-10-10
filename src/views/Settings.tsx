import { useEffect, useState } from 'react';
import type { Settings as S } from '../contract/types';
import { api, type AppState, type SystemInfo } from '../contract/app';
import { AIChoice } from '../setup/Setup';
import { ProfileForm } from '../components/LogForms';
import { AboutPanel } from '../components/About';

interface CompanionState {
  enabled: boolean; running: boolean; address: string | null; lan_ip: string | null;
  devices: { pair_id: string; confirmed: boolean; device: { name: string; model: string } | null; last_sync: string | null; expires_at: string | null }[];
}

function PhoneCompanion({ onChanged }: { onChanged: () => void }) {
  const [st, setSt] = useState<CompanionState | null>(null);
  const [pair, setPair] = useState<{ uri: string; qr_svg: string; expires_at: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<CompanionState>('companion').then(setSt).catch(e => setErr(String(e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!pair) return;
    const t = setInterval(() => api.get<CompanionState>('companion').then(x => {
      setSt(x);
      if (x.devices.some(d => d.confirmed && pair.uri.includes(d.pair_id))) { setPair(null); onChanged(); }
    }), 2000);
    return () => clearInterval(t);
  }, [pair]);
  const act = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } load(); };
  if (!st) return <p className="muted">Checking…</p>;
  return (
    <>
      <p className="muted small">An Android app that reads your watch and devices through Health Connect, sends them here over your home Wi-Fi (encrypted), and gives dose check-ins on time even when this computer is off.</p>
      <label className="choice">
        <input type="checkbox" checked={st.enabled} onChange={e => act(() => api.post('companion/enable', { on: e.target.checked }))} />
        <span><strong>Let a paired phone sync over this home network.</strong> PAi Health then listens on {st.lan_ip ?? 'this computer’s home-network address'} as well as on this computer itself. Only phones that scanned a pairing code can talk to it; everything they send is encrypted.</span>
      </label>
      {st.enabled && !st.running && <p className="warn">This computer isn’t on a private home network right now, so the phone can’t reach it.</p>}
      {st.enabled && st.running && <p className="muted small">If the phone can’t connect, check that Windows treats this Wi-Fi as a <strong>Private network</strong> (Settings → Network &amp; internet → Wi-Fi → your network). Windows blocks the phone on networks marked Public, such as cafés and hotels — which is what you want.</p>}
      {st.running && <p className="muted small">Listening at {st.address}. Windows may ask once whether to allow this on private networks — allow it.</p>}
      {st.running && !pair && <button className="btn-quiet" onClick={() => act(async () => setPair(await api.post('companion/pair', {})))}>Pair a phone</button>}
      {pair && (
        <div className="pairbox">
          <div className="qr" dangerouslySetInnerHTML={{ __html: pair.qr_svg }} />
          <div>
            <p><strong>On the phone:</strong> open PAi Health › Scan the pairing code.</p>
            <p className="muted small">The code works for 10 minutes and only once. It contains the key for this phone — don’t share a photo of it.</p>
            <button className="link" onClick={() => setPair(null)}>Cancel</button>
          </div>
        </div>
      )}
      {st.devices.length > 0 && (
        <ul className="plain-list">
          {st.devices.map(d => (
            <li key={d.pair_id}>
              <strong>{d.device?.model || 'Waiting for the phone…'}</strong>{' '}
              <span className="muted small">{d.confirmed ? `last sync ${d.last_sync?.replace('T', ' ').replace('+00:00', ' UTC') ?? 'never'}` : 'not paired yet'}</span>{' '}
              <button className="link" onClick={() => act(() => api.post('companion/revoke', { pair_id: d.pair_id }))}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {err && <p className="fatal-inline" role="alert">{err}</p>}
    </>
  );
}

function AISetup({ onChanged }: { onChanged: () => void }) {
  const [s, setS] = useState<SystemInfo | null>(null);
  const [app, setApp] = useState<AppState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([api.get<SystemInfo>('system'), api.get<AppState>('app')]).then(([a, b]) => { setS(a); setApp(b); }).catch(e => setErr(String(e)));
  }, []);
  if (err) return <p className="fatal-inline">{err}</p>;
  if (!s || !app) return <p className="muted">Checking this computer…</p>;
  return <AIChoice s={s} ai={app.ai} unlocked onSaved={() => onChanged()} />;
}

export function Settings({ settings, set, audit, live = false, onAIChanged }: {
  settings: S;
  set: <K extends keyof S>(k: K, v: S[K]) => void;
  audit: { at: string; event: string; rule: string }[];
  live?: boolean;
  onAIChanged?: () => void;
}) {
  const [setup, setSetup] = useState(false);
  return (
    <div className="view">
      <header className="view-head">
        <h1>Settings</h1>
        <p className="lede">Only these settings change how PAi Health behaves. Every change is written to the audit log.</p>
      </header>

      {live && <section className="setting">
        <h2>About you</h2>
        <p className="muted small">Used only to pick which yearly labs apply to you (screening recommendations depend on age).</p>
        <ProfileForm />
      </section>}

      {live && <section className="setting">
        <h2>Phone companion</h2>
        <PhoneCompanion onChanged={() => onAIChanged?.()} />
      </section>}

      <section className="setting">
        <h2>How your data arrives</h2>
        <label className="choice">
          <input type="radio" name="path" checked={settings.data_path === 'REVIEW'} onChange={() => set('data_path', 'REVIEW')} />
          <span><strong>Desktop review.</strong> Import exports from your watch and devices. Missed-dose checks and alerts run after each import.</span>
        </label>
        <label className={`choice${settings.live_available ? '' : ' disabled'}`}>
          <input type="radio" name="path" disabled={!settings.live_available} checked={settings.data_path === 'LIVE'} onChange={() => set('data_path', 'LIVE')} />
          <span><strong>Live, through the phone companion.</strong> Reminders arrive within the dose window. {settings.live_available ? '' : 'Pair the phone companion first (above).'}</span>
        </label>
      </section>

      <section className="setting">
        <h2>Count device-recorded doses as confirmed</h2>
        <label className="choice">
          <input type="checkbox" checked={settings.device_dose_confirmation} onChange={e => set('device_dose_confirmation', e.target.checked)} />
          <span>When a smart pen or inhaler sensor records a dose that matches your log, mark that dose as validated. Applies to that single dose only, never to whether a medication is working. Pill caps record opening, not swallowing, so they never count. Exports label these "confirmed by device (your setting)."</span>
        </label>
      </section>

      <section className="setting">
        <h2>AI</h2>
        {(['None', 'PAi', 'PSi'] as const).map(m => (
          <label key={m} className={`choice${m === 'PAi' && !settings.local_model_installed ? ' disabled' : ''}`}>
            <input type="radio" name="ai" checked={settings.ai_mode === m} disabled={m === 'PAi' && !settings.local_model_installed} onChange={() => set('ai_mode', m)} />
            <span>
              {m === 'None' && <><strong>No AI.</strong> Everything still works; text is the plain built-in wording.</>}
              {m === 'PAi' && <><strong>Private AI on this computer.</strong> Nothing leaves your machine.</>}
              {m === 'PSi' && <><strong>Your own API key.</strong> Phrasing requests send summary data to the provider you choose.</>}
            </span>
          </label>
        ))}
        {settings.ai_mode === 'PSi' && <p className="warn">With an API key on, the engine's findings go to your provider to be worded. Document content goes only if you allowed it in AI setup for double-checking uploads.</p>}
        <p className="muted">AI only words things and double-checks what was read from your documents. It never decides a status, a suggestion, an alert, or a lane.</p>
        {live && (setup
          ? <div className="ai-setup"><AISetup onChanged={() => onAIChanged?.()} /></div>
          : <button className="btn-quiet" onClick={() => setSetup(true)}>AI setup — private models or an API key</button>)}
      </section>

      <section className="setting fixed">
        <h2>Always on</h2>
        <p>Emergency messages, the double-dose warning, safety holds on suggestions, medication interaction checks and eating safeguards can't be turned off.</p>
      </section>

      {live && <section className="setting">
        <h2>About and backups</h2>
        <AboutPanel />
      </section>}

      <section>
        <h2 className="section-title">Audit log</h2>
        <table className="audit">
          <tbody>
            {[...audit].reverse().map((a, i) => <tr key={i}><td className="nowrap">{a.at}</td><td>{a.event}</td><td className="muted nowrap">{a.rule}</td></tr>)}
          </tbody>
        </table>
      </section>
    </div>
  );
}
