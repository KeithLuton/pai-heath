# PAi Health: Microsoft Store submission checklist

Work through the steps in order. Items marked **YOU** need your account, artwork or a decision.

## 0. Release blockers (health content, from earlier work)
- [ ] **YOU** Choose the blood pressure home threshold: 135/85 or 140/90.
- [ ] **YOU** Verify the SCENARIO_001 values line by line (`locks.json` notes this as not yet done).
- [ ] Check the library figures against their sources. `tracker/library.py` still has `verified: False`. Nothing marked unverified should reach the public.
- [ ] Freeze the constitution (MEDICAL_PRIMITIVES v0.4.4) for 1.0.

## 1. Partner Center (**YOU**)
- [x] Reserved: Store ID **9PNXSSJWZC9T**, Name `PAiLuton.PAiHealth`, Publisher `CN=D7A83BB4-9693-4195-82DE-2A76C46F6F0F`, PFN `PAiLuton.PAiHealth_tkjdczdb08j36` — now the defaults in build.ps1.
- [ ] Sign in to https://partner.microsoft.com/dashboard as PAi Luton and go to Apps and games > New product > **MSIX or PWA app**. Reserve the name **PAi Health**.
- [ ] Open Product management > **Product identity** and copy three values into the build command:
  - `Package/Identity/Name` → `-IdentityName`
  - `Package/Identity/Publisher` (`CN=…`) → `-Publisher`
  - `Package/Properties/PublisherDisplayName` → `-PublisherDisplayName`
- [ ] Host `store/privacy-policy.html` at https://yprivateai.com/paihealth/privacy. First fill in the date, postal address and email. Also host a support page at /paihealth/support. Both URLs are hard-coded in `src/components/About.tsx`.

## 2. Decisions (**YOU**)
- [ ] **Markets.** Recommendation: **United States only** for 1.0. The lane-3 emergency text says "Call 911", the thresholds follow ACC/AHA, and the literature is NLM/PubMed. Other markets need a localized emergency number first.
- [ ] **Regulatory position.** I'm not a lawyer or a regulatory adviser. In the US, record-keeping, tracking and reminder apps usually fall under the FDA's general-wellness and patient self-management policies. In the UK and EU, software that interprets an individual's readings, such as the BP lanes, can count as a medical device. Get a regulatory opinion before adding UK or EU markets.
- [ ] **Price.** Free, paid, or free trial (Pricing and availability).
- [ ] **Bundle the knowledge base or not.** Bundling makes the package as large as the knowledge base, and each rebuild becomes a full app update. Without it, the literature panels show that none is installed.

## 3. Artwork (**YOU**)
- [ ] Provide a 1024×1024 PNG logo with a transparent background and pass it with `-Icon`. Without it, the build uses a placeholder mark.
- [ ] Take screenshots at 1366×768 or larger using a test user with invented data only. The shot list is in `store/LISTING.md`.

## 4. Build machine (one time)
```powershell
winget install Python.Python.3.12 OpenJS.NodeJS.LTS UB-Mannheim.TesseractOCR Microsoft.WindowsSDK.10.0.26100
```
Turn on Developer Mode (Settings > System > For developers) for local test installs.

## 5. Build, test and install locally
```powershell
cd C:\path\to\pai-health-ui
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\build.ps1 -IdentityName "<Name>" -Publisher "CN=<…>" -Icon C:\art\paihealth-1024.png -KbPath C:\PAiHealth\knowledge\psi_health_kb.db -Register
```
The build script does the following:
- Runs the engine tests and the lock check, and stops if either fails.
- Builds the UI.
- Bundles Tesseract (English) and llama.cpp `llama-server` (Vulkan build, falling back to CPU).
- Writes the third-party notices and the logos.
- Freezes the app with PyInstaller.
- Self-tests the frozen app. The UI, OCR and AI runtime must all be found.
- Builds `resources.pri` and packs `out\PAiHealth_<ver>_x64.msix`.

Test the installed app from the Start menu:
- [ ] First run → hardware card → "No AI" → create a user. The data notice must be ticked.
- [ ] Ingest a PDF and a phone photo of a lab report. Values and source lines appear (OCR works).
- [ ] Private AI: install the smallest model and run a double-check. Confirm `llama-server.exe` exits when the window closes (Task Manager).
- [ ] Phone companion: turn it on, pair, sync. Check that Windows did **not** show a firewall prompt (the manifest rule covers it) and that sync fails on a network marked Public.
- [ ] Make a backup. It lands in Documents\PAi Health Backups.
- [ ] Start a second copy. It shows "PAi Health is already open."
- [ ] Uninstall, then reinstall. The data is gone (expected) and the backup file is still there.
- [ ] Test on a clean **Windows 10 22H2** VM and on **Windows 11**.

## 6. Certification kit
```powershell
.\packaging\build.ps1 -IdentityName "<Name>" -Publisher "CN=<…>" -Icon C:\art\paihealth-1024.png -KbPath C:\PAiHealth\knowledge\psi_health_kb.db -Wack
```
- [ ] `out\wack-report.xml`: Overall PASSED.
- [ ] If Defender or the Store scan flags the PyInstaller launcher (a known false positive), rebuild PyInstaller's bootloader from source (`pip install pyinstaller --no-binary pyinstaller`) and say so in the certification notes.

## 7. Submission (Partner Center)
- [ ] **Packages:** upload `out\PAiHealth_<ver>_x64.msix`. It doesn't need to be signed because the Store signs it. Device family: Desktop.
- [ ] **Properties:**
  - Category: Health & fitness.
  - Privacy policy URL.
  - Support contact.
  - System requirements as in LISTING.md.
- [ ] **Age ratings (IARC questionnaire):** answer truthfully. The app has no violence, sexual content, gambling, user-to-user communication or location sharing, and no in-app purchases. The likely result is 3+/Everyone. The app itself says it is meant for adults.
- [ ] **Store listing:** paste from `store/LISTING.md`, then add the logos (`build\assets\listing\`) and screenshots.
- [ ] **Submission options > Notes for certification:** paste `store/CERTIFICATION_NOTES.txt`.
- [ ] **Restricted capability justification** (`runFullTrust`): "Packaged Win32 desktop app (Python engine with bundled OCR and AI runtime)."
- [ ] Submit. Certification usually takes 1–3 business days. If it's rejected, the report names the policy number; fix the issue and resubmit with the same identity.

## 8. Updates
- Bump `APP_VERSION` in `engine/runtime.py`. The package version becomes `X.Y.Z.0`, which must increase with every upload.
- If what leaves the computer changes, update the privacy policy **before** release. If the data notice text changes, bump `NOTICE_VERSION` in both `engine/runtime.py` and `src/components/About.tsx`.
- The Android companion is a separate app. Its Google Play listing needs its own privacy and Health Connect declarations.
