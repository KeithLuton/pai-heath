// Interop check: the iPhone core against a real PAi Health PC (run by engine/tests/test_companion_swift.py).
// Usage: companion-interop <pairing uri>
// Mirrors android/core-test/CoreTest.java: hello, sync one BP entry and a heart-rate sample, replay must be
// refused, then print the dose prompts ("id<TAB>text") the phone would raise, then "OK".
import CompanionCore
import Foundation

func check(_ ok: Bool, _ what: String) {
    if !ok { print("FAIL \(what)"); exit(1) }
}

let args = CommandLine.arguments
check(args.count >= 2, "usage: companion-interop <pairing uri>")
Pairing.allowTestNet = true
Pairing.allowAnyHostForTests = args.contains("--any-host")
let pairing = try Pairing.parse(args[1])
let store = MemorySeqStore()
let client = SyncClient(pairing: pairing, seqStore: store,
                        device: ["name": "Swift core", "model": "macOS", "app_version": "test"])

let hello = try await client.hello()
check(hello["paired"] as? Bool == true, "hello")
let rules = Rules(bundle: hello["rules"] as? [String: Any] ?? [:])
check(rules.safetyLine.hasPrefix("If you're not sure whether you took it"), "safety line")

let tz = TimeZone.current
let now = Date()
let entry: [String: Any] = ["type": "BP", "systolic": Int64(128), "diastolic": Int64(80),
                            "at": Rules.isoMinute(now.addingTimeInterval(-300), tz)]
let t = Rules.isoMinute(now.addingTimeInterval(-180), tz)
let row: [Any] = [t, t, 66.0, "bpm", "Apple Watch", "health_connect", "WATCH", NSNull(), NSNull(), NSNull(),
                  "HKQuantityTypeIdentifierHeartRate", NSNull()]
let reply = try await client.sync(["entries": [["uid": UUID().uuidString, "entry": entry]],
                                   "samples": ["heart_rate": [row]]])
check(JSON.int(reply["accepted"]) == 1, "accepted \(reply)")

// replay: rewind the counter, the PC must refuse
store.seq = 0
do {
    _ = try await client.sync([:])
    check(false, "replay accepted")
} catch CompanionError.refused(_) {
    // expected
}

let prompts = rules.dosePrompts(now: Date(), timeZone: tz, answered: { _, _ in false }, dosesOn: { _, _ in 0 }, hr: [])
for p in prompts { print("\(p.id)\t\(p.text)") }
check(rules.afterBloodPressure(systolic: 186, diastolic: 100, urgentLast24h: ["chest pain"])?.contains("Call 911") == true, "lane 3")
check(rules.afterBloodPressure(systolic: 186, diastolic: 100, urgentLast24h: [])?.contains("measure again") == true, "recheck")
check(rules.afterBloodPressure(systolic: 130, diastolic: 80, urgentLast24h: []) == nil, "normal")
print("OK")
