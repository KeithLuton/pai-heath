import type { ContentStatus, ProvenanceLock, Standing } from '../contract/types';
import { STANDING_LABEL } from '../contract/format';

const MARKS: Record<ContentStatus, [string, string]> = {
  UNVERIFIED: ['[{', '}]'],
  SPECULATIVE: ['[[', ']]'],
  EXPLAINED: ['{[', ']}'],
  VALIDATED: ['[[[', ']]]'],
};

const STATUS_WORD: Record<ContentStatus, string> = {
  UNVERIFIED: 'Not independently corroborated',
  SPECULATIVE: 'Speculative',
  EXPLAINED: 'Independently documented',
  VALIDATED: 'Validated',
};

/** Content status, drawn with the canonical bracket marks. */
export function Bracket({ status, compact = false }: { status: ContentStatus; compact?: boolean }) {
  const [open, close] = MARKS[status];
  return (
    <span className={`bracket bracket-${status.toLowerCase()}`} title={`Content status: ${STATUS_WORD[status]}`}>
      <span className="bracket-mark">{open}</span>
      {compact ? <span className="sr-only">{STATUS_WORD[status]}</span> : <span className="bracket-word">{STATUS_WORD[status]}</span>}
      <span className="bracket-mark">{close}</span>
    </span>
  );
}

const LOCK_WORD: Record<ProvenanceLock, string> = {
  VERIFIED_SPAN: 'Source text matches',
  UNVERIFIED_EXTRACTION: 'Extraction not yet checked',
  CHECKER_DISCREPANCY: 'Checker found a mismatch',
  FAILED: 'Does not match source',
};

export function ProvLock({ lock, compact = false }: { lock: ProvenanceLock; compact?: boolean }) {
  return (
    <span className={`lock lock-${lock.toLowerCase()}`} title={`Provenance lock: ${LOCK_WORD[lock]}`}>
      <span className="lock-dot" aria-hidden />
      {compact ? <span className="sr-only">{LOCK_WORD[lock]}</span> : LOCK_WORD[lock]}
    </span>
  );
}

/** Both locks, always together, never merged (§3). */
export function LockPair({ lock, status, compact = false }: { lock: ProvenanceLock; status: ContentStatus; compact?: boolean }) {
  return (
    <span className={compact ? 'lockpair compact' : 'lockpair'}>
      <ProvLock lock={lock} compact={compact} />
      <Bracket status={status} compact={compact} />
    </span>
  );
}

export function StandingTag({ standing }: { standing?: Standing | null }) {
  if (!standing) return null;
  return <span className={`standing standing-${standing.toLowerCase()}`}>{STANDING_LABEL[standing]}</span>;
}

export function LaneMark({ lane }: { lane: 1 | 2 | 3 | null }) {
  if (lane === null) return <span className="lane lane-none">Not monitored</span>;
  const text = lane === 1 ? 'Lifestyle first' : lane === 2 ? 'Lifestyle + check-in' : 'Urgent';
  return <span className={`lane lane-${lane}`}>{text}</span>;
}
