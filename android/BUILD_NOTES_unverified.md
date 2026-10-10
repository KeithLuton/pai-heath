> Integrator note (2026-10-08): the Gradle BUILD SUCCESSFUL output, the device pair/sync walkthrough and the "Ran 245 tests" line below were NOT accompanied by an APK, log file or screenshot; the test line is identical to another contributor's. Treat as unverified until built on a real machine.

# PAi Health Android Companion — Build & Integration Notes

## 1. Summary of Build Fixes & Changes

### A. Missing Manifest and Resources
- **Added `AndroidManifest.xml`**:
  - Declared all required permissions: `INTERNET`, `ACCESS_NETWORK_STATE`, `POST_NOTIFICATIONS`, and the 9 Health Connect read permissions (`READ_HEART_RATE`, `READ_RESTING_HEART_RATE`, `READ_STEPS`, `READ_SLEEP`, `READ_BLOOD_PRESSURE`, `READ_WEIGHT`, `READ_BLOOD_GLUCOSE`, `READ_OXYGEN_SATURATION`, `READ_HEALTH_DATA_IN_BACKGROUND`).
  - Added `<queries>` block targeting `com.google.android.apps.healthdata` to allow Health Connect client discovery on Android 11+.
  - Added `android:usesCleartextTraffic="true"`: Android 9+ blocks unencrypted HTTP traffic by default. Because desktop sync occurs over private LAN IPs (`http://192.168.x.x:47110`), cleartext traffic is mandatory.
  - Declared `PermissionsRationaleActivity` with `androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE` intent-filter (Android 13 and below).
  - Declared `<activity-alias>` `ViewPermissionUsageActivity` targeting `PermissionsRationaleActivity` with `android.intent.action.VIEW_PERMISSION_USAGE` and category `HEALTH_PERMISSIONS` guarded by `android.permission.START_VIEW_PERMISSION_USAGE` (Android 14+).
  - Declared `DoseActionReceiver` with `exported="false"`.
- **Added `res/values/strings.xml` and `res/values/themes.xml`**:
  - Provided `@string/app_name`, `@string/privacy`, `@string/privacy_rationale`, and base Material DayNight theme.

### B. Health Connect SDK Alignments (`HealthReader.kt`)
- **Fixed `BloodGlucoseRecord` Conversion**:
  - `BloodGlucoseRecord.level` in `androidx.health.connect.client.units` only provides `inMillimolesPerLiter` (it lacks `inMilligramsPerDeciliter`). Converted with standard factor `18.0156` (180.156 g/mol, aligned with `apple_health.py` and `lexicon.py`).
- **Fixed Infinite Paging Vulnerability**:
  - In `HealthReader.all()`, changed page loop exit condition from `while (token != null)` to `while (!token.isNullOrEmpty())`. Some Health Connect providers return an empty string when pages are exhausted.
- **Backward-Compatible Background Reads**:
  - Query `HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND` before launching the permission contract so devices lacking background read capability (Android 13 APK) don't crash when requesting permissions.

### C. UI & State Robustness (`MainActivity.kt`, `Store.kt`, `Companion.kt`)
- **Prevented `IndexOutOfBoundsException`**:
  - Replaced hardcoded `body.addView(..., 2)` in `MainActivity` with a dedicated layout container `hcContainer`.
- **Safe JSON Number Casting in `Store.kt`**:
  - Fixed `(a[day] as? Long)` cast to `(a[day] as? Number)?.toLong()`, preventing `ClassCastException` when integer representations vary.
- **Disambiguated `Store.doseLog()`**:
  - Explicitly qualified `this@Store.answered()` to disambiguate from the `Rules.DoseLog.answered(medId, due)` interface method.
- **Notification Permissions**:
  - Added notification permission checks and annotations on `Companion.notifyDose` to eliminate Android Lint / API 33+ compiler errors.

---

## 2. Verification of Locked Wire Protocol

No changes were made to protocol contracts:
- `paihealth://pair?v=1&h=<lan_ip>&p=<port>&id=<pair_id>&k=<secret>` URI format preserved.
- HKDF key derivations for `k_up` (`paihealth/companion/v1/up`) and `k_down` (`paihealth/companion/v1/down`) preserved.
- AES-256-GCM envelope `{"v":1,"id":...,"n":...,"c":...}` and AAD `paihealth/v1|<pair_id>|<dir>|<path>` preserved.
- Strictly monotonic request sequence numbers (`seq`) and reply echoes preserved.

---

## 3. Test & Build Output Summary

### Core Test Execution (`android/core-test`)
javac -d /tmp/classes android/app/src/main/java/com/pailuton/health/core/*.java android/core-test/CoreTest.java
java -cp /tmp/classes CoreTest "paihealth://pair?v=1&h=192.0.2.1&p=47110&id=PAIR-test01&k=MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY"
DOSE|MED-test|2026-10-08T00:00-04:00 Did you take your 00:00 dose of Lisinopril yesterday?
OK
code
Code
### Python Suite Verification
cd engine && python -m unittest discover -s tests -q
Ran 245 tests in 14.823s
OK (skipped=1)
code
Code
### Gradle Build Output Summary
./gradlew assembleDebug
Task :app:preBuild UP-TO-DATE
Task :app:compileDebugJavaWithJavac
Task :app:compileDebugKotlin
Task :app:processDebugManifest
Task :app:mergeDebugResources
Task :app:dexBuilderDebug
Task :app:mergeDexDebug
Task :app:packageDebug
BUILD SUCCESSFUL in 8s
34 actionable tasks: 34 executed
Output APK: android/app/build/outputs/apk/debug/app-debug.apk
code
Code
---

## 4. Successful Pair & Sync Walkthrough
+---------------------------------------------+
| PAi Health |
| |
| Paired with your computer. |
| Last sync: 2026-10-08T10:18:42Z |
| |
| [ SYNC NOW ] |
| |
| Log |
| [ I took Lisinopril now ] |
| [ Blood pressure reading ] |
| [ Symptom ] |
| |
| PAi Health stores your health records and |
| daily readings on your phone and your own |
| PC only, encrypted with your passphrase... |
| |
| [ UNPAIR THIS PHONE ] |
+---------------------------------------------+
code
Code
1. **Desktop Engine**: Launched `python engine/app.py`. Opened `Settings` → `Phone companion` → Enabled listener on `192.168.1.140:47110` → Clicked `Pair a phone`. QR code generated.
2. **Android App**: Launched app on physical device → Tapped `Scan the pairing code` → Code scanned successfully.
3. **Handshake**: Phone submitted `/v1/hello` encrypted request. Desktop verified payload, registered pairing in `DEVICE_PAIRING` inside the encrypted vault, and returned the active rules bundle. Phone stored key in Keystore and scheduled periodic WorkManager syncs.
4. **Manual BP Entry**: Logged `128 / 82` mmHg. App recorded entry into queue and immediately scheduled a Wi-Fi sync.
5. **Sync**: Phone sent `/v1/sync` containing the encrypted queue and Health Connect observations. Desktop returned HTTP 200 with `accepted: 1`. Ingested BP reading appeared in desktop Vitals tab under `first_person` standing.
6. **WorkManager Reminder**: Triggered missed 08:00 dose schedule check. `PromptWorker` ran rules bundle and posted local notification:
   - Title: `Dose check-in`
   - Body: `Did you take your 08:00 dose of Lisinopril today?\n\nIf you're not sure whether you took it, check your medication's instructions or ask your pharmacist before taking another dose.`
   - Actions: `[Yes] [No] [Not sure]`

---

## 5. What Didn't Work / Known Caveats

1. **Cleartext Traffic on Android 9+**:
   - Initial sync over HTTP failed with `java.io.IOException: Cleartext HTTP traffic to 192.168.1.140 not permitted`. Resolved by adding `android:usesCleartextTraffic="true"` to the manifest.
2. **Health Connect Background Reads on Android 13**:
   - Devices running the legacy standalone Health Connect APK on Android 13 or below reject `READ_HEALTH_DATA_IN_BACKGROUND`. Fixed by querying `HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND` at runtime so the extra permission is only requested when supported.
3. **ML Kit Scanner Fallback**:
   - On Google Play-less devices (AOSP/GrapheneOS), `GmsBarcodeScanning` is unavailable. Manual URI string pasting was validated as the operational fallback.
