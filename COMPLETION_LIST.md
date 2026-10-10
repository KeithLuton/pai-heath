# PAi Health — completion list (2026-10-10)

Tree state: 305/305 engine tests pass, 39 locked files intact, `tsc` clean, `npm run build` OK (2026-10-10).
Critical path to Store 1.0 = sections A → B → C. D can ship after 1.0.

## A. Decisions only you can make (blocks locking)
- [ ] A1. BP home threshold: 135/85 (current, ACC/AHA home) or 140/90.
- [ ] A2. SCENARIO_001 line-by-line human check → record engine moves TEST_GUARD → LOCKED.
- [ ] A3. Freeze constitution Part I as v1.0 (MEDICAL_PRIMITIVES v0.4.4 today).
- [ ] A4. Check every `tracker/library.py` figure against its cited source (all `verified: False`).
- [ ] A5. Markets (rec: US only), price, bundle KB or not.
- [ ] A6. Regulatory opinion before any UK/EU market.
- [ ] A7. Supply one real device export + one real portal download (PDF or FHIR) → clears UNVERIFIED.

## B. Engineering to finish for 1.0
- [ ] B1. KB rebuild after PubMed corpus finishes on psi-health-builder (ETA ~Oct 11 UTC), then copy `psi_health_kb.db`.
- [x] B2. Billing ingestion — paper (CMS-1500, UB-04, EOB, MSN, patient bill) and FHIR financial resources, Billing panel in Ingest. Done 2026-10-10.
- [ ] B2a. Run one real document of each paper kind (yours, any redacted copy) → tune labels → move off UNVERIFIED. Needs A7-style samples.
- [ ] B2b. Fillable CMS-1500 PDFs: entries live in form fields; flagged today, not read (needs a sample to map field names).
- [ ] B2c. Suppress REPORT Floats from billing text *before* confirmation (record engine is TEST_GUARD; needs a decision on auto-hiding unconfirmed docs).
- [ ] B3. Medical libraries: persist wizard outcomes per user (now session-only); add local file import for LOINC/RxNorm the user downloads; never bundle CPT/NUBC/X12 code tables.
- [ ] B4. Replace seed lexicon (`records/lexicon.py`) with user-installed LOINC/RxNorm (depends on B3).
- [ ] B5. Wire page checker (vision model) so OCR pages can reach VERIFIED_SPAN.
- [ ] B6. Confirm every UI write button reaches the engine (README still lists write paths as visual-only).
- [ ] B7. Run AI investigation against a real local model and one provider; tune bounce/step limit.
- [ ] B8. Backup UI in Settings (restore is in first run; passphrase change is tested).

## C. Store release (Windows machine — store/SUBMISSION_CHECKLIST.md)
- [ ] C1. Host privacy policy (fill date/address/email) at yprivateai.com/paihealth/privacy + support page.
- [ ] C2. 1024×1024 logo; screenshots with invented data.
- [ ] C3. `packaging\build.ps1 ... -Register` → local install test list (§5), Win10 22H2 + Win11.
- [ ] C4. `-Wack` → wack-report PASSED.
- [ ] C5. Partner Center submission (Store ID 9PNXSSJWZC9T).

## D. After 1.0
- [ ] D1. Push the repo to GitHub (private) → Actions builds Android APK + iPhone Simulator build; fix anything the first compile logs show.
- [ ] D1a. Android: install the debug APK, pair, sync; optionally add release-signing secrets for in-place updates.
- [ ] D1b. iPhone: Apple Developer Program + signing secrets → signed IPA → TestFlight; or a Mac with Xcode for a 7-day personal build.
- [ ] D2. Bluetooth on real hardware.
- [ ] D3. Garmin DI_CONNECT, Samsung, Withings, Oura, Polar importers (need samples).
- [ ] D4. DailyMed / MedlinePlus sources; Opposition other-causes links.
- [ ] D5. Optional DPAPI unlock; audit-chain O(n) append before per-sample writes.
- [ ] D6. Billing native adapters (X12 837/835, NCPDP, UBL/CII) — or route to PSi Billing Solver.
