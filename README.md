# PAi Health — UI (v0.1.0)

Desktop UI for PAi Health, built against `MEDICAL_PRIMITIVES.md` v0.4.1.

**The UI renders engine output; it computes nothing epistemic.** Locks, statuses,
lanes, temporal resolution, suggestions, holds and notifications all arrive
precomputed through `src/contract/source.ts`. Today that source is the
SCENARIO_001 fixture plus a synthetic tracker profile (`tools/gen_demo.py`,
seed 55). The Python engine will implement the same contract over loopback,
and `source.ts` is the only file that changes.

What was removed from the Gemini scaffold: the Gemini API, express, the
`0.0.0.0` dev server, and every verdict output (ESTABLISHED/SUPPORTED/etc.).
Fonts are bundled locally; no network requests.

## Run the app (PowerShell)

```powershell
cd pai-health-ui
pip install -r engine\requirements.txt
npm install
npm run build
python engine\app.py --kb C:\PAiHealth\psi_health_kb.db
```

First run: checks this computer and shows the private AI models that fit
(or an API key for NVIDIA Nemotron, OpenAI GPT or Anthropic Claude), then
creates your user (encrypted vault). Then use **Ingest** to add records; every
upload is read, quality-gated and double-checked by the AI you chose.

Private models need the llama.cpp runtime once: `winget install ggml.llamacpp`.

Bluetooth (standard BLE health devices: HR straps, BP cuffs, thermometers,
scales, pulse oximeters) needs one package: `py -m pip install "bleak>=0.22,<2"`.
Then Ingest › Bluetooth devices › Scan › Listen. Readings go into the encrypted
vault as unverified device data with every raw packet kept.
App data: `%LOCALAPPDATA%\PAiHealth` (users, models, config). Copy the
knowledge base to `%LOCALAPPDATA%\PAiHealth\knowledge\` to drop `--kb`.

## Microsoft Store build

`packaging\build.ps1` → `out\PAiHealth_<ver>_x64.msix`. Steps, decisions and Partner Center fields: `store\SUBMISSION_CHECKLIST.md`.
The packaged app opens in its own WebView2 window (`engine\desktop.py`); data under `%LOCALAPPDATA%\PAiHealth` is redirected by MSIX into the package and **removed on uninstall** — backups go to Documents\PAi Health Backups.

## Run (PowerShell) — development

UI development (fixture data, bannered as demo):

```powershell
cd pai-health-ui
npm install
npm run dev        # http://127.0.0.1:5174
```

Through the loopback server (how the product runs):

```powershell
npm run build
python engine\run_fixture.py   # prints and opens http://127.0.0.1:<port>/<token>/
```

## Source modes

- `npm run dev` uses the fixture. Production builds never contain fixture
  data and never fall back to it: if the engine can't be reached, the UI shows
  an error and no data.
- In production the page is served under `/<token>/` and fetches
  `./api/scenario.json`, `./api/tracker.json`, and posts `./api/settings`.
  Every response is wrapped `{contract_version, synthetic, data}`; a version
  mismatch is refused.
- `engine/health_server.py` is the permanent server. `engine/run_fixture.py`
  feeds it the fixture until the Python engine replaces the providers.

Regenerate the synthetic tracker data:

```powershell
python tools\gen_demo.py
```

## Layout

```
engine/health_server.py  loopback server: token prefix, Host check, no CORS, no-referrer
engine/run_fixture.py    dev launcher feeding fixture data (marked synthetic)
```


```
src/contract/types.ts    engine contract (Part I + Part II)
src/contract/source.ts   where engine output comes from (fixture today)
src/contract/format.ts   display-only formatting (never sharpens a date)
src/components/          status marks, provenance drawer, chart, footer
src/views/               Today, Vitals, Medications, Nutrition, Suggestions,
                         Labs, Records, Timeline, Questions, Open items, Settings
src/data/                scenario_001.json (fixture), tracker_demo.json (synthetic)
```

## Known gaps

- Buttons that would write data (log a meal, log readings, import labs, adopt a
  suggestion) are wired visually only; writes go through the engine.
- Some fixture text is developer-facing (rule references like "§10.3.3").
  The engine's renderer will replace it.
- Live mode is disabled until the phone companion exists.
- Packaging into pywebview + MSIX is a later phase.

## Device importers (engine/devices)

| Importer | Status |
|---|---|
| Apple Health (`export.zip` / `export.xml`) | Built; tested on synthetic fixtures only — **UNVERIFIED_AGAINST_REAL_EXPORT** |
| Fitbit / Google Takeout (folder or zip) | Built; synthetic fixtures only — **UNVERIFIED_AGAINST_REAL_EXPORT**. Heart rate/steps treated as UTC, sleep as local in a user-supplied timezone: an assumption until a real export confirms it |
| Garmin Connect `Activities.csv` (workout summaries) | Built; synthetic fixtures only — **UNVERIFIED_AGAINST_REAL_EXPORT**. Units declared by the user; avg/max HR kept as workout summaries only |
| Garmin full account export (`DI_CONNECT`: daily HR, sleep, stress, steps) | Not built — needs a sample |
| Generic CSV with a user mapping (`engine/tests/fixtures/csv/*.mapping.json` shows the format) | Built; status USER_MAPPED — the user's declaration is the interpretation, recorded by hash on every sample |
| Samsung Health export, Withings, Oura, Polar | Not built — waiting on samples |

Run the tests:

```powershell
cd engine
python -m unittest discover -s tests -v
```

Verify against a real export without sharing any health values — the report
holds counts, measure names, source names, date range, rejected records and
unmapped types only:

```powershell
python engine\import_device.py apple  C:\path\to\export.zip > apple_report.json
python engine\import_device.py fitbit C:\path\to\takeout.zip America/New_York > fitbit_report.json
python engine\import_device.py garmin-activities C:\path\to\Activities.csv statute America/New_York > garmin_report.json
python engine\import_device.py csv C:\path\to\file.csv C:\path\to\mapping.json > csv_report.json
```

## Record engine — phase 1 (engine/records)

| Module | Status |
|---|---|
| `segment.py` — sections, sentences, clauses, list items, exact offsets | Built |
| `context.py` — polarity, certainty, temporality, experiencer, with the trigger that set each, and a review list for conflicts | Built |
| Float extraction (concept targets, kind, speaker, dates, roots) | Next |

Known limits, stated plainly:
- Statement-level classification is coarse when one statement holds two
  concepts ("neuropathy without evidence of myelopathy"); it is flagged for
  review, and per-concept classification takes a target span.
- Slash alternatives ("bowel/bladder changes") stay one statement; splitting
  them into Floats is the extractor's job.
- English, fixed header vocabulary.
- The tests were written alongside the code. A held-out set nobody tuned
  against is the real measure of accuracy.

Run everything:

```powershell
cd engine
python -m unittest discover -s tests -v
```

## Ingestion (engine/ingest)

Any supported file -> documents with canonical text, per-page extraction
method, page quality, and exact offsets. Detection is by content, not file
extension. Containers (zip, email, FHIR attachments) are opened recursively,
each member its own document linked to its parent.

| Input | Method | Starting lock |
|---|---|---|
| PDF with text layer | TEXT_LAYER per page | VERIFIED_SPAN |
| Scanned PDF / pages with no usable text | OCR per page (300 dpi, Tesseract), confidence recorded | UNVERIFIED_EXTRACTION |
| Photos, scans, multi-page TIFF faxes | OCR | UNVERIFIED_EXTRACTION |
| Word .docx, .odt, RTF, HTML, email body, CSV, .xlsx | MARKUP | VERIFIED_SPAN |
| Plain text / Markdown (any encoding; non-UTF-8 is warned) | PLAIN | VERIFIED_SPAN |
| FHIR JSON (portal downloads), C-CDA/CCD XML, HL7 v2 | STRUCTURED: coded records kept verbatim with source pointers, plus rendered text | VERIFIED_SPAN |
| ZIP (incl. Apple Health export.zip), .eml with attachments | Containers | per member |

Refused with a plain reason and what to do instead: Outlook .msg, old .doc/.xls,
DICOM image data, HEIC without HEIC support, non-FHIR JSON, password-protected
PDFs without the password, zip bombs, entity-laden XML.

Not yet wired: the page checker (PAi Legal's vision model) that moves OCR pages
from UNVERIFIED_EXTRACTION to VERIFIED_SPAN after comparing the page image to
its text.

```powershell
pip install -r engine\requirements.txt
python engine\ingest_file.py C:\path\to\file.pdf             # summary only, safe to share
python engine\ingest_file.py C:\path\to\file.pdf --show-text # includes the extracted text
```

## Record engine (engine/records): Float, Mix, Gap, Flags, Parliament, renderer

Documents -> segmenter -> context classifier -> Floats -> Mixes -> Gaps/Flags
-> Parliament (Stir) -> rendered summary with lint. General rules only; no
record-specific strings. Gated against SCENARIO_001 (the engine never sees the
expected output) plus an unseen second scenario.

```powershell
cd engine
python -m unittest discover -s tests -v
python run_engine.py C:\path\to\records --question "Could the metformin be causing the numbness?"
```

Fixture issues needing sign-off: FIXTURE_ISSUES in tests/test_record_engine.py.
