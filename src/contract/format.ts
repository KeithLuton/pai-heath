import type { EventDate, Kind, Speaker, Standing, Temporality, Certainty } from './types';

/** Display only. Never sharpens a date: precision and source are always shown. */
export function describeDate(d: EventDate): string {
  if (!d.value || d.precision === 'UNKNOWN') return 'Date not stated';
  if (d.source === 'DOCUMENT_DATE_FALLBACK') return `On or before ${d.value}`;
  if (d.precision === 'APPROXIMATE') return `Approximately ${d.as_stated ?? d.value}`;
  if (d.precision === 'MONTH') return `Sometime in ${d.value}`;
  if (d.precision === 'YEAR') return `Sometime in ${d.value}`;
  return d.value;
}

export const KIND_LABEL: Record<Kind, string> = {
  REPORT: 'Report', OBSERVATION: 'Observation', MEASUREMENT: 'Measurement',
  INTERPRETATION: 'Interpretation', ASSESSMENT: 'Assessment', CODE: 'Code',
  ACTION: 'Action', MEDICATION_STATUS: 'Medication status',
};

export const SPEAKER_LABEL: Record<Speaker, string> = {
  PATIENT: 'Patient', CLINICIAN: 'Clinician', INTERPRETING_SPECIALIST: 'Specialist reading',
  LAB_INSTRUMENT: 'Lab', DEVICE: 'Device', ADMINISTRATIVE: 'Administrative',
  FAMILY_OR_OTHER: 'Family or other', UNKNOWN: 'Not stated',
};

export const STANDING_LABEL: Record<Standing, string> = {
  FIRST_PERSON: 'Logged by you', RELAYED: 'Relayed', CLINICAL: 'From your records', DEVICE: 'From a device',
};

export const TEMPORALITY_LABEL: Record<Temporality, string> = {
  CURRENT: 'Current', HISTORICAL: 'Past', PLANNED: 'Planned', UNSPECIFIED: 'Not specified',
};

export const CERTAINTY_LABEL: Record<Certainty, string> = {
  DEFINITE: 'Stated plainly', HEDGED: 'Hedged ("likely")', HYPOTHETICAL: 'Under consideration ("r/o", differential)',
};

export function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
