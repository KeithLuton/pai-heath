import type { Scenario, Settings, Tracker } from './types';

/**
 * Where the UI gets engine output.
 *
 * - "loopback" (production): the Python engine serves this page under
 *   http://127.0.0.1:<port>/<token>/ and the data under ./api/. Requests are
 *   same-origin and relative, so the UI never handles the port or token.
 * - "fixture" (development only, `npm run dev`): SCENARIO_001 + synthetic
 *   tracker demo, permanently bannered.
 *
 * Loopback mode NEVER falls back to fixture data. If the engine is
 * unreachable the UI shows an error: synthetic health data must never be
 * displayed as if it were the user's.
 */
export const CONTRACT_VERSION = '0.4.4';

export interface Envelope<T> { contract_version: string; synthetic: boolean; data: T }

export interface EngineSource {
  readonly mode: 'fixture' | 'loopback';
  scenario(): Promise<Envelope<Scenario>>;
  tracker(): Promise<Envelope<Tracker>>;
  saveSetting<K extends keyof Settings>(key: K, value: Settings[K]): Promise<void>;
}

export class ContractError extends Error {}

function check<T>(env: Envelope<T>): Envelope<T> {
  if (env.contract_version !== CONTRACT_VERSION) {
    throw new ContractError(`Engine speaks contract ${env.contract_version}; this UI expects ${CONTRACT_VERSION}. Update the app.`);
  }
  return env;
}

async function getJson<T>(path: string): Promise<Envelope<T>> {
  const res = await fetch(path, { cache: 'no-store', headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`The health engine returned ${res.status} for ${path}.`);
  return check((await res.json()) as Envelope<T>);
}

export const loopbackSource: EngineSource = {
  mode: 'loopback',
  scenario: () => getJson<Scenario>('./api/scenario.json'),
  tracker: () => getJson<Tracker>('./api/tracker.json'),
  async saveSetting(key, value) {
    const res = await fetch('./api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PAi-Request': '1' },
      body: JSON.stringify({ key, value }),
    });
    if (!res.ok) throw new Error(`The engine refused the setting change (${res.status}).`);
  },
};

async function fixtureSource(): Promise<EngineSource> {
  const [s, t] = await Promise.all([import('../data/scenario_001.json'), import('../data/tracker_demo.json')]);
  return {
    mode: 'fixture',
    scenario: async () => ({ contract_version: CONTRACT_VERSION, synthetic: true, data: s.default as unknown as Scenario }),
    tracker: async () => ({ contract_version: CONTRACT_VERSION, synthetic: true, data: t.default as unknown as Tracker }),
    async saveSetting() { /* dev only: kept in UI state */ },
  };
}

export async function resolveSource(): Promise<EngineSource> {
  return import.meta.env.DEV ? fixtureSource() : loopbackSource;
}
