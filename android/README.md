# PAi Health — phone companion (Android)

Live mode for PAi Health: dose check-ins on time even when the computer is off, and Health
Connect data (watch, scale, cuff, meter) flowing into the computer's encrypted vault.

## Build

**On GitHub (nothing to install):** push the repo; `.github/workflows/android.yml` builds
`app-debug.apk` and checks the protocol core against the real PC code. Open the run › Artifacts ›
download `PAiHealth-companion-debug`, copy the APK to the phone and open it (allow "Install unknown apps"
for your file manager once).

Debug builds are signed with a throwaway key per run, so each new build must replace the old one
(uninstall first). To update in place, make a release key once and add it as repository secrets:

```powershell
keytool -genkeypair -v -keystore paihealth-release.jks -alias paihealth -keyalg RSA -keysize 4096 -validity 10000
[Convert]::ToBase64String([IO.File]::ReadAllBytes("paihealth-release.jks")) | Set-Clipboard
```

Secrets: `ANDROID_KEYSTORE_B64` (the clipboard), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`
(`paihealth`), `ANDROID_KEY_PASSWORD`. Keep the `.jks` file safe; losing it means users must reinstall.

**In Android Studio (Windows):**

1. Install Android Studio (it brings the Android SDK and a JDK).
2. File › Open › this `android` folder. Let it sync; accept any suggested patch updates of library versions.
3. Plug in the phone with USB debugging on (Settings › About › tap Build number 7 times › Developer options › USB debugging).
4. Run ▶. The app installs as "PAi Health".

## Pair

On the computer: PAi Health › Settings › Phone companion › Turn on › Pair a phone.
On the phone: Scan the pairing code. Same home Wi-Fi required. Windows will ask once whether
PAi Health may accept connections on private networks — allow it.

## What it does

* Reads Health Connect (heart rate, resting heart rate, steps, sleep, blood pressure, weight,
  glucose, SpO₂) — read only — and sends it to the computer every ~15 minutes on Wi-Fi.
* Logs doses, blood pressure and symptoms ("logged by you"); they wait on the phone until synced.
* Raises dose check-ins from the computer's rules (same words, same timing), always with the
  double-dose safety line; Yes / No / Not sure right on the notification.
* After a very high blood-pressure reading: the recheck message, or — with an urgent symptom
  logged — the fixed emergency message with a Call 911 button.

## Security

* Pairing secret travels only in the QR code; it's stored under an Android Keystore key.
* Every request/response is AES-256-GCM sealed (keys from HKDF of the secret), bound to its
  path and direction, with a strictly increasing sequence number — replays are refused.
* The computer listens only on its private home-network address, only while the companion is
  on, and only answers sealed requests from paired phones.

## Status

`core/` (protocol, crypto, rules) is plain Java, compiled and tested against the real PC server
(`engine/tests/test_companion_java.py`). The Android layer (`app/`) has not been compiled yet —
no Android SDK could be downloaded where it was written. Reviewed line by line on 2026-10-10
(Health Connect pinned to the stable 1.1.0, lifecycle-ktx added, the `Companion` object renamed
`PhoneSync`); the first CI run is the compile check. `BUILD_NOTES_unverified.md` describes an
earlier outside build claim that came with no APK or log — ignore its "BUILD SUCCESSFUL".
