# PAi Health — phone companion (iPhone)

Same job as the Android companion: dose check-ins on time even when the computer is off, Apple
Health data (watch, cuff, scale, meter) flowing into the computer's encrypted vault, and quick
logging of doses, blood pressure and symptoms. Same pairing QR code, same protocol, same rules,
same wording.

## Layout

| Path | What |
|---|---|
| `CompanionCore/` | Swift package: protocol envelope (CryptoKit HKDF + AES-256-GCM), pairing, sync client, dose and blood-pressure rules. No UI. |
| `CompanionCore/Tests/` | Known-answer tests from `Resources/vectors.json` — keys, ciphertext and envelopes from the PC protocol; 40 dose prompts and 5 blood-pressure cases from the Java core. Regenerate with `python tools/gen_companion_vectors.py`. |
| `CompanionCore/Sources/CompanionInterop` | Command-line phone that pairs and syncs with a live PC server (`engine/tests/test_companion_swift.py`). |
| `App/` | SwiftUI app: pairing (camera or pasted link), HealthKit reading, check-in notifications with Yes / No / Not sure, logging, background refresh. |
| `project.yml` | XcodeGen spec (Info.plist permissions, HealthKit entitlement, background refresh). |

## Build

Nothing to install on Windows. GitHub builds it on a Mac runner:

* `.github/workflows/ios.yml` runs the core tests, the live interop test against the PC server,
  and a Simulator build of the app (no Apple account needed for any of that).
* With Apple signing secrets set, it also archives a signed `.ipa`.

On a Mac instead: `brew install xcodegen && cd ios && xcodegen generate && open PAiHealthCompanion.xcodeproj`.

## Putting it on an iPhone

Apple only lets signed apps onto a phone. Pick one:

| Route | Needs | Notes |
|---|---|---|
| **TestFlight** (recommended) | Apple Developer Program ($99/yr) | Upload the CI `.ipa` with Apple's Transporter app, then install from TestFlight. Re-upload every 90 days. |
| **Ad-hoc** | Developer Program + the phone's UDID registered | CI `.ipa` installs directly (Apple Configurator, or Finder on a Mac). |
| **Personal build from a Mac** | A Mac with Xcode, a free Apple ID | Xcode ▶ to the plugged-in phone. Expires after 7 days. |

CI signing secrets (Settings › Secrets and variables › Actions):
`IOS_CERT_P12_B64` (Apple Distribution certificate, base64), `IOS_CERT_PASSWORD`,
`IOS_PROFILE_B64` (provisioning profile for `com.pailuton.health.companion` with HealthKit, base64),
`IOS_TEAM_ID`. Optional `IOS_EXPORT_METHOD` (`ad-hoc` or `development`; `app-store` is chosen automatically
for a profile without devices).

## Differences from Android, stated plainly

* **Background time.** iOS decides when an app may run in the background. Syncing happens when the
  app opens, after a check-in answer, and in background refresh when iOS allows it — not on a fixed
  15-minute timer.
* **Check-ins are scheduled ahead.** So they arrive on time without the app running, the next 48 hours of
  check-ins are scheduled as notifications. Those use the plain question ("Did you take your 08:00 dose
  of …?"). The heart-rate wording ("Looks like you may have taken…") needs the reading after the dose,
  so it appears only for check-ins raised while the app is running — exactly as the computer would word them.
* **Health permissions.** iOS never tells an app whether reading was allowed. Types you didn't allow just
  arrive empty.
* **Samples are labeled `healthkit`** in the vault (Android's are `health_connect`).

## Security

* The pairing link (holds the shared secret) is kept in the Keychain, this device only.
* Queued entries, rules and the sequence counter are in a file under iOS Data Protection.
* Same wire protocol as Android: every body AES-256-GCM sealed, bound to path and direction,
  strictly increasing sequence numbers; replays refused by the computer.
* Talks only to the computer's private home-network address (10.x, 172.16–31.x, 192.168.x).

## Status

Written 2026-10-10 without a Mac: **not yet compiled**. The first CI run is the compile check; expect to
fix small API mismatches from its log. The core is held to the PC and the Java core by vectors and by a
live interop test, which also run in CI.

## Build preparation (2026-10-10)

Xcode language mode is Swift 5.0 (the package tools requirement remains 5.9).
The JSON helper imports CoreFoundation explicitly and identifies floating NSNumber
values without an NSNumber-to-CFNumber cast. UIKit is explicitly imported for the app delegate.
The simulator job preserves xcodebuild's exit status, uploads build.log and its
xcresult on success or failure, and uploads the simulator app on success.
These edits have been checked statically only; no Xcode/Swift compiler was available.
The simulator artifact cannot be installed on an iPhone; that needs a signed build.

To compile: put the project contents at a GitHub repository root, then open
Actions > iPhone companion > Run workflow. No signing secrets are needed for
the core tests or simulator build. Wait for both jobs to pass. If either fails,
download ios-build-diagnostics and use its compiler errors to finish the fixes.
