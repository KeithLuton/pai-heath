import { useEffect, useState } from 'react';
import { api } from '../contract/app';

/** Must match engine/runtime.py NOTICE_VERSION. Changing the text below means a new version. */
export const NOTICE_VERSION = 'PAIH-DATA-NOTICE-v1';
export const PRIVACY_URL = 'https://yprivateai.com/paihealth/privacy';
export const SUPPORT_URL = 'https://yprivateai.com/paihealth/support';

export function DataNotice() {
  return (
    <div className="notice">
      <h3>Before you start</h3>
      <ul>
        <li>PAi Health keeps the health information you add — documents, readings, medications, device exports — <strong>on this computer only</strong>, encrypted with your passphrase. PAi Luton never receives it. There is no account and no cloud copy.</li>
        <li>Information leaves this computer only if you turn that on: an outside AI provider you choose (you'll see a separate warning and consent), or a phone you pair on your home Wi-Fi.</li>
        <li><strong>PAi Health is not a medical device.</strong> It organises your records and points out things to discuss with your clinician; it does not diagnose or treat, and it doesn't replace medical advice. In an emergency, call your local emergency number.</li>
        <li>Uninstalling PAi Health deletes its data on this computer. Make backups in Settings → About and backups.</li>
      </ul>
      <p className="small"><a href={PRIVACY_URL} target="_blank" rel="noreferrer">Privacy policy</a></p>
    </div>
  );
}

type About = { version: string; notice_version: string; packaged: boolean; backup_folder: string; ocr: boolean };

function ChangePassphrase() {
  const [old, setOld] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = async () => {
    if (next !== again) { setMessage({ ok: false, text: "The new passphrases don't match." }); return; }
    if (next.length < 12) { setMessage({ ok: false, text: 'At least 12 characters are required.' }); return; }
    setBusy(true); setMessage(null);
    try {
      await api.post('users/passphrase', { old, new: next });
      setOld(''); setNext(''); setAgain('');
      setMessage({ ok: true, text: 'Vault passphrase changed.' });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  };
  return <>
    <h3>Change passphrase</h3>
    <p className="warn">Your records are encrypted with your passphrase. There is no reset: if it's lost, the data can't be recovered. Write it down somewhere safe.</p>
    <p className="muted small">Existing encrypted backups still need the passphrase used when each backup was made. Changing the vault passphrase does not update old backups.</p>
    <form className="userform" onSubmit={e => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>Current passphrase</span><input type="password" autoComplete="current-password" value={old} onChange={e => setOld(e.target.value)} required /></label>
      <label className="field"><span>New passphrase — at least 12 characters</span><input type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} required /></label>
      <label className="field"><span>New passphrase again</span><input type="password" autoComplete="new-password" value={again} onChange={e => setAgain(e.target.value)} required /></label>
      <div className="row"><button className="btn" type="submit" disabled={busy || old.length === 0 || next.length < 12 || again.length === 0}>{busy ? 'Changing…' : 'Change passphrase'}</button></div>
      {message && <p role="status" className={message.ok ? 'ok' : 'fatal-inline'}>{message.text}</p>}
    </form>
  </>;
}

export function AboutPanel() {
  const [a, setA] = useState<About | null>(null);
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api.get<About>('about').then(setA).catch(() => setA(null)); }, []);

  const backup = async () => {
    if (pw !== pw2) { setMsg({ ok: false, text: "The passphrases don't match." }); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await api.post<{ path: string }>('backup', { passphrase: pw });
      setMsg({ ok: true, text: `Saved: ${r.path}` }); setPw(''); setPw2('');
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    setBusy(false);
  };

  return (
    <div className="about">
      <p><strong>PAi Health</strong> {a ? `version ${a.version}` : ''} · PAi Luton</p>
      <p className="muted">Not a medical device. PAi Health organises your records and suggests things to discuss with your clinician. It does not diagnose, treat, or replace medical advice. In an emergency, call your local emergency number.</p>

      <h3>Backups</h3>
      <p className="warn">Uninstalling PAi Health deletes everything it stores on this computer. Back up first.</p>
      <p className="muted small">An encrypted backup file is written to {a ? <code>{a.backup_folder}</code> : 'your Documents folder'}. It needs its own passphrase to restore; without it the backup can't be opened by anyone, including us. To restore: lock, choose New user, then Restore from a backup.</p>
      <form className="userform" onSubmit={e => { e.preventDefault(); backup(); }}>
        <label className="field"><span>Backup passphrase <span className="muted">— at least 12 characters</span></span><input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="new-password" /></label>
        <label className="field"><span>Backup passphrase again</span><input type="password" value={pw2} onChange={e => setPw2(e.target.value)} autoComplete="new-password" /></label>
        <div className="row"><button className="btn" type="submit" disabled={busy || pw.length < 12}>{busy ? 'Backing up…' : 'Make a backup'}</button></div>
        {msg && <p className={msg.ok ? 'ok' : 'fatal-inline'} role="status">{msg.text}</p>}
      </form>

      <ChangePassphrase />

      <h3>Privacy and licences</h3>
      <p className="small"><a href={PRIVACY_URL} target="_blank" rel="noreferrer">Privacy policy</a> · <a href={SUPPORT_URL} target="_blank" rel="noreferrer">Support</a></p>
      <p className="muted small">This product uses publicly available data from the U.S. National Library of Medicine (NLM), National Institutes of Health, Department of Health and Human Services; NLM is not responsible for the product and does not endorse or recommend this or any other product.</p>
      <p className="muted small">Includes llama.cpp (MIT) and Tesseract OCR (Apache-2.0). Full third-party notices are in THIRD_PARTY_NOTICES.txt in the install folder.</p>
    </div>
  );
}
