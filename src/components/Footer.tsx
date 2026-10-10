export function Disclaimer() {
  return (
    <p className="disclaimer">
      PAi Health organizes your records, tracks your data, and offers general wellness suggestions.
      It does not diagnose, prescribe, or replace your clinician. Decisions belong to you and your licensed clinicians.
    </p>
  );
}

/** Lane 3 text is fixed and is never rephrased by a model (§28.3). */
export function UrgentMessage() {
  return (
    <div className="urgent" role="alert">
      <strong>Contact your clinician or emergency services now.</strong>
      <span> If you have chest pain, sudden weakness, trouble speaking or breathing, call emergency services.</span>
    </div>
  );
}
