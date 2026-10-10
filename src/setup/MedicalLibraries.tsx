import { useState } from 'react';

export const MEDICAL_LIBRARIES = [
  { id: 'cms', name: 'CMS / CDC medical codes', url: 'https://www.cms.gov/medicare/coding-billing', detail: 'ICD-10, HCPCS, MS-DRG and place-of-service reference sources.' },
  { id: 'fda', name: 'FDA drug identifiers', url: 'https://www.accessdata.fda.gov/scripts/cder/ndc/', detail: 'National Drug Code directory.' },
  { id: 'loinc', name: 'LOINC laboratory terminology', url: 'https://loinc.org/license/', detail: 'Review the Regenstrief license and obtain LOINC.' },
  { id: 'rxnorm', name: 'RxNorm medication terminology', url: 'https://www.nlm.nih.gov/research/umls/rxnorm/', detail: 'Review NLM distribution and third-party source terms.' },
  { id: 'snomed', name: 'SNOMED CT US Edition', url: 'https://www.nlm.nih.gov/healthit/snomedct/', detail: 'Register and obtain the appropriate affiliate authorization.' },
  { id: 'cpt', name: 'AMA CPT procedure terminology', url: 'https://www.ama-assn.org/practice-management/cpt/cpt-licensing', detail: 'Obtain CPT access under the terms applicable to your use.' },
  { id: 'nubc', name: 'AHA / NUBC hospital codes', url: 'https://www.nubc.org/', detail: 'Obtain the hospital coding reference from its publisher.' },
  { id: 'x12', name: 'X12 adjustment and remark codes', url: 'https://x12.org/', detail: 'Check publisher access and permitted uses.' },
] as const;

const DRAFT_KEY = 'pai-health-medical-library-selection-v1';
function initialSelection(): string[] {
  try { const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null');
    if (Array.isArray(saved)) return MEDICAL_LIBRARIES.filter(x => saved.includes(x.id)).map(x => x.id);
  } catch { /* browsing storage is optional */ }
  return MEDICAL_LIBRARIES.map(x => x.id);
}

/** This is an onboarding/handoff wizard, NOT a license acceptance mechanism or installed-library verifier. */
export function MedicalLibraries({ onDone }: { onDone: () => void }) {
  const [selected, setSelected] = useState<string[]>(initialSelection);
  const [index, setIndex] = useState(-1);
  const [understood, setUnderstood] = useState(false);
  const [outcomes, setOutcomes] = useState<Record<string, 'deferred' | 'user-reported'>>({});
  const chosen = MEDICAL_LIBRARIES.filter(x => selected.includes(x.id));
  const current = chosen[index];
  const setChoices = (v: string[]) => {
    setSelected(v);
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(v)); } catch { /* optional */ }
  };
  const advance = (status: 'deferred' | 'user-reported') => {
    if (current) setOutcomes(o => ({ ...o, [current.id]: status }));
    setIndex(i => i + 1); setUnderstood(false);
  };
  return <section className="card" aria-label="Medical reference libraries">
    {index < 0 ? <>
      <h1>Medical reference libraries</h1>
      <p>PAi Health can use medical terminology stored separately on your computer. Choose the publishers you want to visit. You can finish or skip any step and return later.</p>
      <p className="muted">These datasets are provided by independent publishers, not licensed to you by PAi Health. Each publisher's terms govern your use. Downloading or viewing a page does not establish that you have accepted or obtained a license.</p>
      <button type="button" className="btn-quiet" onClick={() => setChoices(selected.length === MEDICAL_LIBRARIES.length ? [] : MEDICAL_LIBRARIES.map(x => x.id))}>{selected.length === MEDICAL_LIBRARIES.length ? 'Deselect all' : 'Select all'}</button>
      {MEDICAL_LIBRARIES.map(x => <label className="choice" key={x.id}><input type="checkbox" checked={selected.includes(x.id)} onChange={e => setChoices(e.target.checked ? [...selected, x.id] : selected.filter(id => id !== x.id))}/><span>{x.name}</span></label>)}
      <div className="row"><button type="button" className="btn" onClick={() => setIndex(0)}>{chosen.length ? `Continue with ${chosen.length} libraries` : 'Continue without libraries'}</button></div>
    </> : current ? <>
      <p className="muted">Library {index + 1} of {chosen.length}</p>
      <h1>{current.name}</h1><p>{current.detail}</p>
      <div className="callout"><strong>Separate publisher agreement</strong><p>You obtain this reference data directly from its publisher, for uses authorized by the publisher's terms. PAi Health does not grant rights in the dataset. You—not PAi Health—must complete any registration, payment, or acceptance required by that publisher.</p></div>
      <p><a href={current.url} target="_blank" rel="noopener noreferrer">Open the publisher's page in your browser</a></p>
      <label className="choice"><input type="checkbox" checked={understood} onChange={e => setUnderstood(e.target.checked)}/><span>I understand this dataset is separate and the publisher's terms apply. This is not acceptance of the publisher's license.</span></label>
      <div className="row"><button type="button" className="btn" disabled={!understood} onClick={() => advance('user-reported')}>I've completed this publisher step</button><button type="button" className="btn-quiet" onClick={() => advance('deferred')}>Skip for now</button><button type="button" className="btn-quiet" onClick={() => { setIndex(i => i - 1); setUnderstood(false); }}>Back</button></div>
      <p className="muted small">PAi Health cannot verify an agreement or install a reference file from this page. You can connect files when that integration is available. No license is represented as installed.</p>
    </> : <>
      <h1>Medical library setup reviewed</h1>
      <p>{Object.values(outcomes).filter(x => x === 'user-reported').length} publisher steps marked complete by you; {Object.values(outcomes).filter(x => x === 'deferred').length} deferred. No downloads, license grants, or offline lookup installations have been verified.</p>
      <p className="muted">PAi Health can be used without optional terminology files. Missing definitions must remain unresolved rather than guessed.</p>
      <button type="button" className="btn" onClick={onDone}>Continue to PAi Health</button>
    </>}
  </section>;
}
