import { useState } from 'react';
import { api } from '../contract/app';

/** First-person entries (§19). What you log is the record — "logged by you". */
export async function logEntry(entry: Record<string, unknown>) {
  return api.post<{ id: string; entry: Record<string, unknown> }>('log', entry);
}

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}
/** "2026-10-08T07:30" in this computer's zone -> "2026-10-08T07:30-04:00" (the reading keeps its local clock time). */
export const withZone = (v: string) => {
  if (!v) return undefined;
  const off = -new Date(v).getTimezoneOffset();
  const sign = off >= 0 ? '+' : '-';
  const hh = String(Math.floor(Math.abs(off) / 60)).padStart(2, '0'), mm = String(Math.abs(off) % 60).padStart(2, '0');
  return `${v}${sign}${hh}:${mm}`;
};

function useSubmit(onDone: () => void) {
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (entry: Record<string, unknown>, done: string) => {
    setBusy(true); setErr(null); setOk(null);
    try { await logEntry(entry); setOk(done); onDone(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  return { err, ok, busy, submit };
}

function Msgs({ err, ok }: { err: string | null; ok: string | null }) {
  return <>{ok && <p className="ok small">{ok}</p>}{err && <p className="fatal-inline" role="alert">{err}</p>}</>;
}

export function ReadingForms({ onLogged }: { onLogged: () => void }) {
  const [kind, setKind] = useState<'BP' | 'GLUCOSE' | 'WEIGHT' | 'SYMPTOM' | 'ITEM'>('BP');
  const [at, setAt] = useState(localNow());
  const [a, setA] = useState(''); const [b, setB] = useState(''); const [c, setC] = useState('');
  const [fasting, setFasting] = useState(true);
  const [unit, setUnit] = useState<'kg' | 'lb'>('lb');
  const [urgent, setUrgent] = useState<string[]>([]);
  const { err, ok, busy, submit } = useSubmit(() => { setA(''); setB(''); setC(''); setUrgent([]); onLogged(); });
  const URGENT = ['chest pain', 'shortness of breath', 'sudden weakness or numbness', 'trouble speaking', 'vision change', 'severe headache', 'confusion'];
  const go = () => {
    const when = withZone(at);
    if (kind === 'BP') submit({ type: 'BP', systolic: a, diastolic: b, pulse: c || undefined, at: when }, `Logged ${a}/${b}.`);
    if (kind === 'GLUCOSE') submit({ type: 'GLUCOSE', mg_dl: a, fasting, at: when }, `Logged ${a} mg/dL.`);
    if (kind === 'WEIGHT') submit({ type: 'WEIGHT', kg: unit === 'kg' ? Number(a) : Number(a) * 0.45359237, at: when }, `Logged ${a} ${unit}.`);
    if (kind === 'SYMPTOM') submit({ type: 'SYMPTOM', text: a, urgent, at: when }, 'Logged.');
    if (kind === 'ITEM') submit({ type: 'ITEM', name: a, at: when }, `Logged ${a}.`);
  };
  return (
    <div className="logform">
      <div className="doc-tabs">
        {([['BP', 'Blood pressure'], ['GLUCOSE', 'Glucose'], ['WEIGHT', 'Weight'], ['SYMPTOM', 'Symptom'], ['ITEM', 'Something I took']] as const).map(([k, l]) =>
          <button key={k} className={kind === k ? 'tab on' : 'tab'} onClick={() => setKind(k)}>{l}</button>)}
      </div>
      <div className="row wrap">
        {kind === 'BP' && <>
          <label className="field sm"><span>Systolic</span><input inputMode="numeric" value={a} onChange={e => setA(e.target.value)} /></label>
          <label className="field sm"><span>Diastolic</span><input inputMode="numeric" value={b} onChange={e => setB(e.target.value)} /></label>
          <label className="field sm"><span>Pulse (optional)</span><input inputMode="numeric" value={c} onChange={e => setC(e.target.value)} /></label>
        </>}
        {kind === 'GLUCOSE' && <>
          <label className="field sm"><span>mg/dL</span><input inputMode="numeric" value={a} onChange={e => setA(e.target.value)} /></label>
          <label className="toggle-inline"><input type="checkbox" checked={fasting} onChange={e => setFasting(e.target.checked)} /> Fasting (8+ hours, no food)</label>
        </>}
        {kind === 'WEIGHT' && <>
          <label className="field sm"><span>Weight</span><input inputMode="decimal" value={a} onChange={e => setA(e.target.value)} /></label>
          <label className="field sm"><span>Unit</span><select value={unit} onChange={e => setUnit(e.target.value as 'kg' | 'lb')}><option>lb</option><option>kg</option></select></label>
        </>}
        {kind === 'SYMPTOM' && <label className="field grow"><span>What you noticed</span><input value={a} onChange={e => setA(e.target.value)} placeholder="e.g. tingling in both feet" /></label>}
        {kind === 'ITEM' && <label className="field grow"><span>What you took</span><input value={a} onChange={e => setA(e.target.value)} placeholder="e.g. ibuprofen 400 mg, energy drink" /></label>}
        <label className="field sm"><span>When</span><input type="datetime-local" value={at} max={localNow()} onChange={e => setAt(e.target.value)} /></label>
      </div>
      {kind === 'SYMPTOM' && (
        <fieldset className="perms"><legend>Any of these right now?</legend>
          {URGENT.map(u => <label key={u} className="toggle-inline urgent-pick"><input type="checkbox" checked={urgent.includes(u)}
            onChange={e => setUrgent(x => e.target.checked ? [...x, u] : x.filter(y => y !== u))} /> {u}</label>)}
          {urgent.length > 0 && <p className="urgent">If this is happening now, call 911 or your local emergency number.</p>}
        </fieldset>
      )}
      {kind === 'BP' && <p className="muted small">Seated with your back supported, 5 minutes rest, arm at heart level, cuff on bare upper arm.</p>}
      <button className="btn" disabled={busy || (!a && kind !== 'SYMPTOM') || (kind === 'SYMPTOM' && !a && !urgent.length) || (kind === 'BP' && !b)} onClick={go}>Log it</button>
      <Msgs err={err} ok={ok} />
    </div>
  );
}

export interface MedDraft { med_id?: string; name: string; dose: string; schedule: string[]; start: string; targets: string[]; stopped?: string }

export function MedicationForm({ initial, onSaved, onCancel }: { initial?: MedDraft; onSaved: () => void; onCancel: () => void }) {
  const [m, setM] = useState<MedDraft>(initial ?? { name: '', dose: '', schedule: ['08:00'], start: '', targets: [] });
  const { err, busy, submit } = useSubmit(onSaved);
  const T: [string, string][] = [['bp', 'Blood pressure'], ['resting_hr', 'Resting heart rate'], ['glucose', 'Fasting glucose'], ['weight', 'Weight']];
  return (
    <div className="logform">
      <div className="row wrap">
        <label className="field grow"><span>Medication</span><input value={m.name} onChange={e => setM({ ...m, name: e.target.value })} placeholder="e.g. Lisinopril" /></label>
        <label className="field sm"><span>Dose</span><input value={m.dose} onChange={e => setM({ ...m, dose: e.target.value })} placeholder="10 mg" /></label>
        <label className="field sm"><span>Started (optional)</span><input type="date" value={m.start} onChange={e => setM({ ...m, start: e.target.value })} /></label>
      </div>
      <div className="row wrap">
        <span className="small muted">Usual times:</span>
        {m.schedule.map((s, i) => (
          <span key={i} className="row">
            <input type="time" value={s} onChange={e => setM({ ...m, schedule: m.schedule.map((x, j) => j === i ? e.target.value : x) })} />
            <button className="link" onClick={() => setM({ ...m, schedule: m.schedule.filter((_, j) => j !== i) })}>remove</button>
          </span>
        ))}
        <button className="btn-quiet" onClick={() => setM({ ...m, schedule: [...m.schedule, '20:00'] })}>Add a time</button>
      </div>
      <fieldset className="perms"><legend>What do you want to watch before and after starting it? (optional)</legend>
        {T.map(([k, l]) => <label key={k} className="toggle-inline urgent-pick"><input type="checkbox" checked={m.targets.includes(k)}
          onChange={e => setM({ ...m, targets: e.target.checked ? [...m.targets, k] : m.targets.filter(x => x !== k) })} /> {l}</label>)}
        <p className="muted small">PAi Health never decides what a medication should affect — you or your clinician choose.</p>
      </fieldset>
      <div className="row">
        <button className="btn" disabled={busy || !m.name.trim()} onClick={() => submit({ type: 'MEDICATION', ...m, start: m.start || null }, 'Saved.')}>Save</button>
        {initial?.med_id && <button className="btn-quiet" onClick={() => submit({ type: 'MEDICATION', ...m, start: m.start || null, stopped: new Date().toISOString().slice(0, 10) }, 'Marked stopped.')}>Mark as stopped</button>}
        <button className="btn-quiet" onClick={onCancel}>Cancel</button>
      </div>
      <Msgs err={err} ok={null} />
    </div>
  );
}

export function ProfileForm() {
  const [by, setBy] = useState(''); const [sex, setSex] = useState('');
  const { err, ok, busy, submit } = useSubmit(() => undefined);
  return (
    <div className="row wrap">
      <label className="field sm"><span>Birth year</span><input inputMode="numeric" value={by} onChange={e => setBy(e.target.value)} placeholder="1971" /></label>
      <label className="field sm"><span>Sex</span><select value={sex} onChange={e => setSex(e.target.value)}>
        <option value="">—</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option><option value="prefer not to say">Prefer not to say</option></select></label>
      <button className="btn-quiet" disabled={busy} onClick={() => submit({ type: 'PROFILE', birth_year: by || null, sex: sex || null }, 'Saved. Lab suggestions now use your age.')}>Save</button>
      <Msgs err={err} ok={ok} />
    </div>
  );
}

/* ---------------------------------------------------------------- meals (USDA FoodData Central) */
export interface MealDraftItem {
  fdc_id: number;
  description: string;
  grams: number;
  portion_label?: string;
  energy_kcal?: number;
  protein_g?: number;
}

export interface SearchFoodItem {
  fdc_id: number;
  description: string;
  food_category: string;
  energy_kcal: number;
  protein_g: number;
}

export interface FoodPortionOption {
  id: number;
  amount: number;
  modifier: string;
  description: string;
  gram_weight: number;
}

export interface FoodDetailsResponse {
  fdc_id: number;
  description: string;
  food_category: string;
  publication_date: string;
  portions: FoodPortionOption[];
  nutrients_per_100g: Record<string, number>;
}

export function MealForm({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const [at, setAt] = useState(localNow());
  const [note, setNote] = useState('');
  const [items, setItems] = useState<MealDraftItem[]>([]);
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchFoodItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [selectedFood, setSelectedFood] = useState<SearchFoodItem | null>(null);
  const [portions, setPortions] = useState<FoodPortionOption[]>([]);
  const [loadingPortions, setLoadingPortions] = useState(false);
  const [selectedPortionId, setSelectedPortionId] = useState<number>(0);
  const [portionMultiplier, setPortionMultiplier] = useState<string>('1');
  const [customGrams, setCustomGrams] = useState<string>('100');
  const [nutrients100g, setNutrients100g] = useState<Record<string, number>>({});
  const { err, ok, busy, submit } = useSubmit(onSaved);

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearchErr(null);
    try {
      const res = await api.get<{ foods: SearchFoodItem[] }>('foods/search?q=' + encodeURIComponent(query.trim()));
      setSearchResults(res.foods || []);
      if (!res.foods || res.foods.length === 0) {
        setSearchErr('No matching foods found in USDA FoodData Central.');
      }
    } catch (e) {
      setSearchErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  };

  const pickFood = async (food: SearchFoodItem) => {
    setSelectedFood(food);
    setLoadingPortions(true);
    setPortionMultiplier('1');
    try {
      const details = await api.get<FoodDetailsResponse>('foods/portions/' + food.fdc_id);
      setPortions(details.portions || []);
      setNutrients100g(details.nutrients_per_100g || {});
      if (details.portions && details.portions.length > 0) {
        setSelectedPortionId(details.portions[0].id);
      } else {
        setSelectedPortionId(0);
      }
    } catch {
      setPortions([{ id: 0, amount: 100, modifier: 'g', description: '100 g', gram_weight: 100 }]);
      setSelectedPortionId(0);
    } finally {
      setLoadingPortions(false);
    }
  };

  let grams = 100;
  let portionLabel = '100 g';
  if (selectedPortionId === -99) {
    grams = Math.max(1, Math.min(3000, Number(customGrams) || 100));
    portionLabel = `${grams} g`;
  } else {
    const selPortion = portions.find(p => p.id === selectedPortionId);
    const mult = Math.max(0.1, Number(portionMultiplier) || 1);
    if (selPortion) {
      grams = Math.round(selPortion.gram_weight * mult);
      portionLabel = mult === 1 ? selPortion.description : `${mult} × ${selPortion.description} (${grams} g)`;
    }
  }

  const estCalories = nutrients100g['energy_kcal'] != null ? Math.round((nutrients100g['energy_kcal'] / 100) * grams) : undefined;
  const estProtein = nutrients100g['protein_g'] != null ? Math.round(((nutrients100g['protein_g'] / 100) * grams) * 10) / 10 : undefined;

  const addItem = () => {
    if (!selectedFood) return;
    setItems(prev => [
      ...prev,
      {
        fdc_id: selectedFood.fdc_id,
        description: selectedFood.description,
        grams,
        portion_label: portionLabel,
        energy_kcal: estCalories,
        protein_g: estProtein,
      },
    ]);
    setSelectedFood(null);
    setSearchResults([]);
    setQuery('');
  };

  const removeItem = (index: number) => {
    setItems(prev => prev.filter((_, i) => i !== index));
  };

  const handleSave = () => {
    if (items.length === 0) return;
    submit(
      {
        type: 'MEAL',
        at: withZone(at),
        items: items.map(it => ({
          fdc_id: it.fdc_id,
          description: it.description,
          grams: it.grams,
          portion_label: it.portion_label,
        })),
        note: note.trim() || undefined,
      },
      `Logged meal with ${items.length} item${items.length === 1 ? '' : 's'}.`
    );
  };

  return (
    <div className="logform">
      <div className="row wrap">
        <label className="field grow">
          <span>Search USDA food</span>
          <div className="row">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } }}
              placeholder="e.g. rolled oats, blueberries, salmon"
            />
            <button className="btn-quiet" type="button" onClick={doSearch} disabled={searching || !query.trim()}>
              {searching ? 'Searching…' : 'Search'}
            </button>
          </div>
        </label>
        <label className="field sm">
          <span>When</span>
          <input type="datetime-local" value={at} max={localNow()} onChange={e => setAt(e.target.value)} />
        </label>
      </div>

      {searchErr && <p className="muted small">{searchErr}</p>}

      {searchResults.length > 0 && !selectedFood && (
        <ul className="food-results" aria-label="Search results">
          {searchResults.map(f => (
            <li key={f.fdc_id} className="food-result-item" onClick={() => pickFood(f)}>
              <span><strong>{f.description}</strong> {f.food_category && <span className="muted small">· {f.food_category}</span>}</span>
              <span className="muted small">{Math.round(f.energy_kcal)} kcal/100g</span>
            </li>
          ))}
        </ul>
      )}

      {selectedFood && (
        <div className="selected-food-box">
          <p><strong>Selected:</strong> {selectedFood.description}</p>
          {loadingPortions ? (
            <p className="muted small">Loading portion options…</p>
          ) : (
            <div className="row wrap">
              <label className="field grow">
                <span>Portion measure</span>
                <select value={selectedPortionId} onChange={e => setSelectedPortionId(Number(e.target.value))}>
                  {portions.map(p => (
                    <option key={p.id} value={p.id}>{p.description}</option>
                  ))}
                  <option value="-99">Custom grams</option>
                </select>
              </label>

              {selectedPortionId === -99 ? (
                <label className="field sm">
                  <span>Grams</span>
                  <input
                    type="number"
                    min="1"
                    max="3000"
                    value={customGrams}
                    onChange={e => setCustomGrams(e.target.value)}
                  />
                </label>
              ) : (
                <label className="field sm">
                  <span>Quantity</span>
                  <input
                    type="number"
                    step="0.25"
                    min="0.25"
                    max="50"
                    value={portionMultiplier}
                    onChange={e => setPortionMultiplier(e.target.value)}
                  />
                </label>
              )}

              <div className="field sm">
                <span>Estimate</span>
                <span className="small muted" style={{ paddingTop: '8px' }}>
                  {estCalories != null ? `${estCalories} kcal` : ''}{estProtein != null ? ` · ${estProtein}g protein` : ''} ({grams} g)
                </span>
              </div>

              <button className="btn" type="button" onClick={addItem}>Add to meal</button>
              <button className="btn-quiet" type="button" onClick={() => setSelectedFood(null)}>Cancel</button>
            </div>
          )}
        </div>
      )}

      {items.length > 0 && (
        <>
          <h3 className="section-title" style={{ fontSize: '15px', marginTop: '12px' }}>Items in this meal ({items.length})</h3>
          <ul className="meal-draft-list">
            {items.map((it, idx) => (
              <li key={idx} className="meal-draft-item">
                <span>
                  <strong>{it.description}</strong> — {it.portion_label || `${it.grams} g`}
                  {it.energy_kcal != null && <span className="muted small"> · {it.energy_kcal} kcal</span>}
                </span>
                <button className="link" onClick={() => removeItem(idx)}>remove</button>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="row wrap" style={{ marginTop: '8px' }}>
        <label className="field grow">
          <span>Note (optional)</span>
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. Breakfast at home" />
        </label>
      </div>

      <div className="row" style={{ marginTop: '10px' }}>
        <button className="btn" disabled={busy || items.length === 0} onClick={handleSave}>
          {busy ? 'Saving…' : 'Save meal'}
        </button>
        <button className="btn-quiet" onClick={onCancel}>Cancel</button>
      </div>
      <Msgs err={err} ok={ok} />
    </div>
  );
}