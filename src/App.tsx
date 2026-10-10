import { useEffect, useState } from 'react';
import { resolveSource, type EngineSource } from './contract/source';
import type { Scenario, Settings as S, Tracker } from './contract/types';
import { FloatDrawer } from './components/Drawer';
import { Disclaimer } from './components/Footer';
import { Today } from './views/Today';
import { Records } from './views/Records';
import { Timeline } from './views/Timeline';
import { Questions, OpenItems } from './views/Questions';
import { Medications } from './views/Medications';
import { Vitals } from './views/Vitals';
import { Nutrition } from './views/Nutrition';
import { Suggestions } from './views/Suggestions';
import { Labs } from './views/Labs';
import { Settings } from './views/Settings';
import { Ingest } from './views/Ingest';
import { Explore } from './views/Explore';
import { SetupWizard, UnlockScreen } from './setup/Setup';
import { api, type AppState } from './contract/app';

const NAV: { group: string; items: { id: string; label: string }[] }[] = [
  { group: '', items: [{ id: 'today', label: 'Today' }] },
  { group: 'Tracking', items: [
    { id: 'vitals', label: 'Vitals' }, { id: 'meds', label: 'Medications' },
    { id: 'nutrition', label: 'Nutrition' }, { id: 'suggestions', label: 'Suggestions' }, { id: 'labs', label: 'Labs' },
  ] },
  { group: 'Records', items: [
    { id: 'ingest', label: 'Ingest' }, { id: 'records', label: 'Records' }, { id: 'timeline', label: 'Timeline' },
    { id: 'questions', label: 'Your questions' }, { id: 'open', label: 'Open items' },
  ] },
  { group: '', items: [{ id: 'settings', label: 'Settings' }] },
];

function stamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

type Loaded = { source: EngineSource; scenario: Scenario; tracker: Tracker; synthetic: boolean; app: AppState | null };

export default function App() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [gate, setGate] = useState<AppState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const source = await resolveSource();
        let app: AppState | null = null;
        if (source.mode === 'loopback') {
          app = await api.get<AppState>('app');
          if (!app.user) { if (live) { setGate(app); setLoaded(null); } return; }
        }
        const [sc, tr] = await Promise.all([source.scenario(), source.tracker()]);
        if (live) { setGate(null); setLoaded({ source, scenario: sc.data, tracker: tr.data, synthetic: sc.synthetic || tr.synthetic, app }); }
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { live = false; };
  }, [nonce]);

  const reload = () => setNonce(n => n + 1);
  if (error) {
    return (
      <div className="fatal" role="alert">
        <h1>PAi Health can't reach its engine</h1>
        <p>{error}</p>
        <p>No data is shown rather than showing anything that might not be yours. Close and reopen PAi Health; if this keeps happening, restart the computer.</p>
      </div>
    );
  }
  if (gate) return gate.users.length ? <UnlockScreen state={gate} onReady={reload} /> : <SetupWizard onReady={reload} />;
  if (!loaded) return <div className="fatal"><p>Loading your data…</p></div>;
  return <Shell key={nonce} {...loaded} reload={reload} />;
}

function Shell({ source, scenario: initialScenario, tracker: initialTracker, synthetic, app, reload }: Loaded & { reload: () => void }) {
  const [tracker, setTracker] = useState(initialTracker);
  const refreshTracker = () => source.tracker().then(tr => setTracker(tr.data)).catch(() => undefined);
  const [route, setRoute] = useState(app && Object.keys(initialScenario.documents ?? {}).length === 0 ? 'ingest' : 'today');
  const [scenario, setScenario] = useState(initialScenario);
  const [appState, setAppState] = useState(app);
  const refreshData = () => {
    source.scenario().then(sc => setScenario(sc.data)).catch(() => undefined);
    refreshTracker();
    if (app) api.get<AppState>('app').then(setAppState).catch(() => undefined);
  };
  const lock = async () => { await api.post('users/lock', {}); reload(); };
  const [selected, setSelected] = useState<string | null>(null);
  const [settings, setSettings] = useState<S>(tracker.settings);
  const [audit, setAudit] = useState(tracker.audit);
  const [saveError, setSaveError] = useState<string | null>(null);

  const set = <K extends keyof S>(k: K, v: S[K]) => {
    if (settings[k] === v) return;
    const prev = settings[k];
    setSettings(s => ({ ...s, [k]: v }));
    source.saveSetting(k, v).then(() => {
      setSaveError(null);
      setAudit(a => [...a, { at: stamp(), event: `Setting changed: ${String(k)} from ${String(prev)} to ${String(v)}`, rule: 'user setting' }]);
    }).catch(e => {
      setSettings(s => ({ ...s, [k]: prev }));
      setSaveError(e instanceof Error ? e.message : String(e));
    });
  };

  const fact = selected ? scenario.expected.floats.find(f => f.id === selected) ?? null : null;
  const go = (r: string) => { setRoute(r); setSelected(null); };

  return (
    <div className={`app${fact ? ' with-drawer' : ''}`}>
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><span className="brand-pai">PAi</span> Health</div>
        {NAV.map((g, gi) => (
          <div key={gi} className="nav-group">
            {g.group && <p className="nav-group-label">{g.group}</p>}
            {g.items.map(it => (
              <button key={it.id} className={route === it.id ? 'nav-item on' : 'nav-item'} aria-current={route === it.id ? 'page' : undefined} onClick={() => go(it.id)}>
                {it.label}
              </button>
            ))}
          </div>
        ))}
        <div className="sidebar-foot">
          {appState?.user && <p className="who">{appState.user.name}</p>}
          <p>AI: {settings.ai_mode === 'PAi' ? 'private, on this computer' : settings.ai_mode === 'PSi' ? (appState?.ai.external?.label ?? 'your API key') : 'off'}</p>
          <p>Data: {settings.data_path === 'REVIEW' ? 'desktop review' : 'live'}</p>
          {appState && <button className="link light" onClick={lock}>Lock</button>}
        </div>
      </nav>

      <main className="main">
        {synthetic && !app && (
          <div className="synthetic">Demo data: SCENARIO_001 fixture (not yet signed off) and a synthetic tracker profile. Not a real person.</div>
        )}

        {saveError && <div className="fatal-inline" role="alert">{saveError}</div>}
        {route === 'today' && <Today t={tracker} settings={settings} go={go} live={!!app} onLogged={refreshTracker} />}
        {route === 'vitals' && <Vitals t={tracker} live={!!app} onLogged={refreshTracker} />}
        {route === 'meds' && <Medications t={tracker} settings={settings} live={!!app} onLogged={refreshTracker} />}
        {route === 'nutrition' && <Nutrition t={tracker} live={!!app} onLogged={refreshTracker} />}
        {route === 'suggestions' && <Suggestions t={tracker} live={!!app} onLogged={refreshTracker} />}
        {route === 'labs' && <Labs t={tracker} go={go} />}
        {route === 'ingest' && appState && <Ingest ai={{ ...appState.ai, mode: settings.ai_mode }} goSettings={() => go('settings')} onChanged={refreshData} />}
        {route === 'records' && <Records s={scenario} select={setSelected} selected={selected} />}
        {route === 'timeline' && <Timeline s={scenario} select={setSelected} />}
        {route === 'questions' && <Questions s={scenario} select={setSelected} />}
        {route === 'questions' && app && <Explore current={scenario.expected.stir?.opening.verbatim ?? null} select={setSelected} onAsked={refreshData} />}
        {route === 'open' && <OpenItems s={scenario} select={setSelected} />}
        {route === 'settings' && <Settings settings={settings} set={set} audit={audit} live={!!app} onAIChanged={() => { refreshData(); reload(); }} />}
        <Disclaimer />
      </main>

      {fact && <FloatDrawer fact={fact} scenario={scenario} onClose={() => setSelected(null)} />}
    </div>
  );
}
