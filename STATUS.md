# PAi Health — status (2026-10-08)

Constitution: `MEDICAL_PRIMITIVES.md` v0.4.4 (draft, not frozen). Contract version 0.4.4.
Tests: 305 passing (2026-10-10) (`cd engine; python -m unittest discover -s tests`).

## Lock tiers

| Tier | Meaning | Change procedure |
|---|---|---|
| **LOCKED** | File frozen by SHA-256 | `python tools/lock.py relock <path> --reason "..."` — bumps version, logs history |
| **TEST_GUARD** | The test file is frozen; the code it tests may change while it stays green | Same — tests can't be weakened silently |
| **OPEN** | Still being shaped | Normal edits |

`engine/tests/test_locks.py` fails the suite on any unrecorded change to a locked file, and on any mismatch of the contract version between the UI, the server and the engine.

## Component map

| Component | Files | Tier | State |
|---|---|---|---|
| Canonical arithmetic | `engine/spigot/*` (pinned from Mental Metals V24) | LOCKED | Floor composition, structural constants |
| Store packaging | `engine/desktop.py`, `engine/runtime.py`, `packaging/*`, `store/*` | Manifest LOCKED; TEST_GUARD (`test_packaging.py`) | Frozen build self-tests on Linux; **Windows build/MSIX/WACK not yet run** |
| Loopback server | `engine/health_server.py` | LOCKED | Token prefix, Host check, no CORS, no-referrer, write gate; security-tested |
| UI contract | `src/contract/types.ts`, `source.ts` | LOCKED | No fallback to demo data in production |
| Ingest data model | `engine/ingest/model.py` | LOCKED | Canonical text, page method, starting lock |
| Device sample model | `engine/devices/schema.py` | LOCKED | Normalized sample, provenance, method-specific measures |
| Temporal rules | `engine/records/dates.py` | LOCKED | §7.2 resolution, fallback = upper bound, approximate = order only |
| Segmenter + context classifier | `records/segment.py`, `context.py` | TEST_GUARD (`test_records.py`) | 37 cases incl. adversarial; accuracy on real notes unmeasured |
| Text ingestion (all formats) | `ingest/ingest.py`, `formats_*.py` | TEST_GUARD (`test_ingest.py`) | Not yet run on a real portal download |
| Page-image ingestion + download | `ingest/pipeline.py`, `pdf_pages.py`, `download.py` | TEST_GUARD (`test_page_ingest.py`) | Feeds the page checker |
| Device importers | `devices/apple_health.py`, `fitbit.py`, `garmin_activities.py`, `generic_csv.py` | TEST_GUARD | All UNVERIFIED_AGAINST_REAL_EXPORT |
| Lab-report extraction | `records/labs.py` (ported from extract_labs, §2.5 fixes) | TEST_GUARD (`test_labs.py`) | Quest/Labcorp columns, flags, censored, qualitative, serial draws |
| SCENARIO_001 fixture | `src/data/scenario_001.json` | LOCKED | 8/8 decisions; line-by-line human check pending |
| Record engine | `records/floats.py`, `mixes.py`, `gaps.py`, `parliament.py`, `render.py`, `pipeline.py` | TEST_GUARD (`test_record_engine.py`) | Passes the SCENARIO_001 gate + unseen SCENARIO_002 |
| Encrypted storage | `engine/storage/keys.py`, `store.py`, `backup.py` | TEST_GUARD (`test_storage.py`) | AES-256-GCM per row, scrypt-wrapped data key, row-bound AAD, append-only + supersede, audited purge, encrypted backup/restore |
| Audit chain | `engine/storage/audit.py` (verbatim from PAi Legal) | LOCKED | HMAC-SHA256 chain; ids and ciphertext hashes only, no content |
| Knowledge base | `engine/knowledge/build_kb.py` (VM), `kb.py` (app), `tools/vm/kb_build.sh` | TEST_GUARD (`test_knowledge.py`) | MeSH qualifier links (adverse effects→chemically induced, complications→etiology), counts by design, ≤20 PMIDs/link, retractions out, no titles/abstracts; exact entry-term retrieval feeds the Parliament |
| App (users, AI setup, ingest) | `engine/app.py`, `app_state.py`, `src/setup/Setup.tsx`, `src/views/Ingest.tsx` | TEST_GUARD (`test_app.py`) | First run scans hardware → private models or API key → user vault; Ingest tab uploads, gates, AI double-checks, person resolves |
| AI layer | `engine/ai/*` (hardware, catalog, local_ai ported from PAi Legal; providers.py new) | OPEN | llama-server on loopback w/ per-run key; Nemotron/GPT/Claude on fixed HTTPS endpoints, model list fetched from provider |
| Ingest double-check | `engine/review/check.py`, `pages.py` | TEST_GUARD (`test_app.py`) | Discrepancy must quote extracted text exactly or is discarded; only moves provenance lock; never content status |
| Bluetooth LE | `engine/devices/bluetooth/*`, `src/views/Bluetooth.tsx` | TEST_GUARD (`test_bluetooth.py`) | GATT HR/BP/temp/weight/SpO₂ decode; raw packets + DeviceSamples into vault; UNVERIFIED on hardware |
| AI investigation | `engine/investigate/agent.py`, `src/views/Explore.tsx` | TEST_GUARD (`test_investigate.py`) | Read-only tool loop; proposals stay MODEL_PROPOSAL `[[ ]]`; ids/quotes/§12 wording/citations enforced in code; user adopts a proposal as a question |
| Phone companion (PC side) | `engine/companion/*` | protocol LOCKED; TEST_GUARD (`test_companion.py`) | LAN listener (RFC 1918, off by default), QR pairing, AES-GCM sealed sync, replay refusal, rules bundle |
| Phone companion (Android) | `android/` | core: TEST_GUARD via `test_companion_java.py`; app: NOT COMPILED | Java core interop-tested against the PC; Kotlin app reviewed 2026-10-10, builds in GitHub Actions (`.github/workflows/android.yml`) — first run is the compile check |
| Phone companion (iPhone) | `ios/` | core: TEST_GUARD via `test_companion_swift.py` + Swift vector tests; app: NOT COMPILED | Swift core (CryptoKit) held to PC/Java by `vectors.json` and a live interop test; SwiftUI app (HealthKit, scheduled check-ins, logging, BG refresh). Builds on GitHub macOS runners (`ios.yml`); signed IPA needs Apple Developer secrets |
| Seed lexicon | `records/lexicon.py` | OPEN | Replace with LOINC / RxNorm / SNOMED |
| UI views | `src/views/*`, `components/*` | OPEN | Write paths visual only |
| Tracker logic | `engine/tracker/compute.py`, `library.py`, `logs.py` | TEST_GUARD (`test_tracker.py`) | Lanes (BP/glucose/weight), lane-3 fixed text, dose prompts + safety line, learned dose pattern, change windows, §26 suggestions with gates and hold list, §27 labs asks, §25.5 interaction checks. All library figures `verified: False` |
| Device exports → vault | `engine/tracker/device_store.py` | TEST_GUARD (`test_tracker.py`) | Apple/Fitbit/Garmin/CSV detected on upload, compressed per-measure chunks, dedupe across imports |
| Billing extraction | `engine/billing/*`, `src/views/Billing.tsx`, registry `ingest/billing_profiles.py` | TEST_GUARD (`test_billing_extract.py`, `test_billing_profiles.py`) | Paper: CMS-1500, UB-04, EOB, MSN, patient bill (person confirms the form type). FHIR: Claim, ClaimResponse, EOB, Invoice, PaymentNotice, PaymentReconciliation (structural). All UNVERIFIED against real documents |
| PubMed knowledge base | VM `psi-health-builder` | building | 1–34 and 935–1093 done; resume running 35–934, 1094–1334 |

## Phone companions (2026-10-10)

- **Android:** reviewed for a first build; Health Connect pinned to stable 1.1.0, lifecycle-ktx added, `Companion`
  object renamed `PhoneSync` (it shadowed Kotlin's companion-object name). CI builds a debug APK; release APK when
  signing secrets exist.
- **iPhone (new):** `ios/CompanionCore` Swift package (envelope, pairing, sync, rules) + `ios/App` SwiftUI app.
  Same pairing QR and protocol as Android. Check-ins for the next 48 h are scheduled as local notifications with the
  plain question; heart-rate wording only when raised live (can't be known ahead). Samples labeled `healthkit`
  (PC `companion/service.py` now accepts that label).
- **Cross-checks:** `tools/gen_companion_vectors.py` writes `vectors.json` — keys/ciphertext/envelopes from the PC
  protocol and 40 dose prompts + 5 BP texts from the Java core; Swift tests must reproduce them. 
  `engine/tests/test_companion_swift.py` runs the Swift core against a live PC server (skips without Swift).
- Neither app has been compiled yet: no Android SDK or Swift toolchain is downloadable in the build workspace.
  GitHub Actions is the compile step.

## Billing extraction (2026-10-10)

`engine/billing/` reads billing documents into source assertions under the registry's provenance contract.
- **Paper** (CMS-1500, UB-04, EOB, Medicare Summary Notice, patient bill): printed markers only *suggest* a form;
  a person confirms it (`BILLING_PROFILE` vault record, superseded on change, never deleted). Then label-driven
  extraction: line tables from header labels (up to three stacked header lines), header fields from labels.
- **Where values come from:** plain text is read from its original layout (column gaps kept) and mapped to
  canonical offsets; PDF text layers by word positions (page + bbox in pt, plus canonical offsets when unique);
  scans and photos by Tesseract word boxes (bbox + per-word OCR confidence). Every value round-trips (tested).
- **Rules enforced in code:** money columns map only by explicit labels ("Amount"/"Total" alone stay
  `amount_unqualified`); nothing derived by arithmetic; repeated rows kept and flagged (possible double billing);
  printed totals only compared with lines (flag, no change); conflicting header values stay unknown; codes stay
  opaque; paid fields carry `paid-as-reported`; fillable-PDF entries flagged as unread.
- **FHIR** financial resources read without confirmation (resourceType is structural); JSON-pointer locators;
  numbers keep their printed digits; adjudication/total amounts labeled only by their own category
  (missing → CATEGORY_MISSING); version undeclared → read as R4 and said so; R5 renames flagged.
- **Kept out of the record engine:** once confirmed as billing, a document yields no clinical Floats and is
  excluded from the Parliament scenario. Before confirmation, billing text still produces REPORT Floats
  (existing behaviour of `records/floats.py`).
- Fixtures are synthetic (`tools/gen_billing_fixtures.py`). Every layout is UNVERIFIED_AGAINST_REAL_FORM.

## Merge of 2026-10-10

- Medical-library onboarding (`src/setup/MedicalLibraries.tsx`) added as setup step 4 of 5. Ported onto the
  current `Setup.tsx` by hand — the patch was cut from an older copy and would have removed Restore from backup.
  Embedded publisher iframe removed: the LOCKED server CSP (`default-src 'self'`) blocks all frames, so it could
  only ever show a blank box. Link opens in the system browser. Outcomes are session-only (see COMPLETION_LIST B3).
- Billing/invoice ingestion registry added as `engine/ingest/billing_profiles.py` (60 profiles, descriptions
  only, no code tables). Not yet called by the ingest pipeline (COMPLETION_LIST B2).
- New TEST_GUARD: `test_billing_profiles.py`, `test_medical_library_setup.py`. 275 tests passing.

## Merge of 2026-10-08

One tree. From the second merge (`pai-health-merged.zip`) only the lab-layout handling was kept, ported
into `records/labs.py` with three §2.5 fixes (no default units, no copied reference units, no added
specimens) and lab flags as separate INTERPRETATION Floats. Its Parliament still loaded gaps, flags and
refusals from the fixture's answer key, and `float_extract.py` duplicated `records/floats.py`; both dropped.

## Retired in the earlier merge

`engine/fms/`, `engine/parliament/`, `run_fms.py`, `extract_labs.py` and their tests: hardcoded scenario
strings, an engine that writes its own question (breaks §9), a file that didn't parse, and lab extraction
that invents units. The hostile lab-layout cases are kept in `engine/tests/pending/` as targets.

Kept from that tree: Apple Health extra types, Fitbit calories/distance, Garmin missing-field checks,
the HTTPS downloader and page-image ingestion.

## Waiting on you before more can lock

1. **SCENARIO_001** — all 8 decisions made 2026-10-08 and the fixture is now LOCKED. One step remains:
   a person checking every expected value line by line (the fixture says so in `verification.status`).
   After that, the record engine moves from TEST_GUARD to LOCKED.
2. **Constitution freeze** — Part I (§0–§16) is implemented and tested; freezing it as v1.0 lets the
   record engine lock against it. Part II stays draft until the tracker is built.
3. **One real export** for any device importer, and one real portal download (PDF or FHIR), to move
   those out of UNVERIFIED.

## Next build steps

2. Knowledge base: rebuild when the PubMed baseline finishes (first build covers the shards done so far);
   add DailyMed / MedlinePlus as higher-level sources; feed the Opposition other-causes links (X -> finding).
3. Wire the page checker (vision model) so OCR pages can reach VERIFIED_SPAN.
4. Storage follow-ups: Windows DPAPI protector so the app can unlock without retyping (optional,
   user setting); audit-chain append is O(n) per write (PAi Legal design) — fine for batched device
   imports, revisit before per-sample writes; UI for backup/restore and passphrase change.

## §17 amended (v0.4.2, owner decision 2026-10-08)

External providers may double-check uploads with consent: per provider, per scope (page
images / source lines), consent text `PSI-INGEST-CONSENT-v1` accepted in the UI and recorded
(CONSENT_GIVEN / CONSENT_WITHDRAWN audit events; widening the scope asks again). Server refuses
sharing without it (409). Whole documents are still never sent.

## Phone companion (2026-10-08, v0.4.4)

Android first, same home Wi-Fi, prompts on the phone. Build in Android Studio (android/README.md).
First real build will likely need small fixes in the Kotlin layer (library versions, Health Connect
API names). The Java core and the PC side are tested together here.

## Tracker (2026-10-08)

Tracking pages now run on the person's own logs, device imports and records — no demo profile once a
user is open. Not built: meal logging / food totals (USDA FoodData Central), typed lab entry, live
(phone) mode. Every library figure and threshold carries `verified: False` until a person checks it
against the cited source. Home BP lane thresholds use ACC/AHA home equivalents (lane 2 at 135/85 home ≈
140/90 office), not the constitution's illustrative 140/90.

## Investigation (v0.4.3, owner decision 2026-10-08)

"Maximum creative freedom, zero authority to fabricate evidence." Questions page: Ask (Parliament)
or Explore with AI. Built here rather than merged — the other build that described
POST /api/investigate wasn't uploaded. Not yet run against a real model: local small models may
need several bounced attempts per rule; the step limit is 14.

## Bluetooth LE (merged 2026-10-08 from pai-health-bluetooth-test.zip)

`engine/devices/bluetooth/` — TEST_GUARD (`test_bluetooth.py`). Fixed from the test build:
SFLOAT -INF/reserved values, weight 0xFFFF read as 327.675 kg, device timestamps ignored
(stored cuff/scale readings were dated at receive time), BP pulse rate dropped, plaintext
JSONL output (now vault only; plaintext only behind --unencrypted-test-file). Not run on real
hardware yet.

## Storage (2026-10-08)

Vault directory = `vault.key` (scrypt-wrapped random data key) + `health.db` (SQLite, every payload
AES-256-GCM) + `audit.log` (keyed chain). Readable without the passphrase: row numbers, record kind,
opaque keyed tags. Not readable: names, values, dates, file names, document hashes.
Corrections supersede, never overwrite (copy-forward). `purge()` is the only delete and is audited.
`verify()` cross-checks every row against the chain: inserted, altered, swapped, relabelled,
deleted, or foreign rows are reported. No passphrase recovery by design; the backup is the recovery
path. `run_engine.py --vault DIR [--new-vault]` stores original documents and settings; settings
posted from the UI persist and are audited.
