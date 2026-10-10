import CryptoKit
import XCTest
@testable import CompanionCore

/// Holds the iPhone core to the PC protocol and the Java rules, via Resources/vectors.json
/// (tools/gen_companion_vectors.py). Regenerate the vectors when the PC rules change.
final class CompanionCoreTests: XCTestCase {
    static let vectors: [String: Any] = {
        let url = Bundle.module.url(forResource: "vectors", withExtension: "json")!
        return try! JSON.object(Data(contentsOf: url))
    }()

    var crypto: [String: Any] { Self.vectors["crypto"] as! [String: Any] }

    func hex(_ k: SymmetricKey) -> String { k.withUnsafeBytes { Data($0) }.map { String(format: "%02x", $0) }.joined() }

    func data(hex: String) -> Data {
        var d = Data()
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            d.append(UInt8(hex[i..<j], radix: 16)!)
            i = j
        }
        return d
    }

    func testKeysMatchPC() {
        let secret = Envelope.b64d(crypto["secret_b64"] as! String)!
        let k = Envelope.keys(secret: secret, pairId: crypto["pair_id"] as! String)
        XCTAssertEqual(hex(k.up), crypto["k_up_hex"] as! String)
        XCTAssertEqual(hex(k.down), crypto["k_down_hex"] as! String)
        XCTAssertEqual(String(data: Envelope.aad(pairId: crypto["pair_id"] as! String, direction: "up", path: "/v1/hello"),
                              encoding: .utf8), crypto["aad_up_hello"] as? String)
    }

    func testRawAESGCMMatchesPC() throws {
        let secret = Envelope.b64d(crypto["secret_b64"] as! String)!
        let pid = crypto["pair_id"] as! String
        let k = Envelope.keys(secret: secret, pairId: pid)
        let raw = crypto["raw"] as! [String: Any]
        let out = try Envelope.sealRaw(key: k.up, nonce: data(hex: raw["nonce_hex"] as! String),
                                       plaintext: Data((raw["plaintext"] as! String).utf8),
                                       aad: Envelope.aad(pairId: pid, direction: "up", path: "/v1/hello"))
        XCTAssertEqual(out.map { String(format: "%02x", $0) }.joined(), raw["ciphertext_and_tag_hex"] as! String)
    }

    func testOpensPCEnvelopesAndRefusesTampering() throws {
        let secret = Envelope.b64d(crypto["secret_b64"] as! String)!
        let pid = crypto["pair_id"] as! String
        let k = Envelope.keys(secret: secret, pairId: pid)
        for e in crypto["envelopes"] as! [[String: Any]] {
            let dir = e["direction"] as! String
            let got = try Envelope.open(key: dir == "up" ? k.up : k.down, pairId: pid, direction: dir,
                                        path: e["path"] as! String, body: Data((e["body"] as! String).utf8))
            XCTAssertEqual(NSDictionary(dictionary: got), NSDictionary(dictionary: e["payload"] as! [String: Any]))
        }
        let t = crypto["tamper"] as! [String: Any]
        let body = Data((t["body"] as! String).utf8)
        XCTAssertThrowsError(try Envelope.open(key: k.down, pairId: pid, direction: "down", path: t["wrong_path"] as! String, body: body))
        XCTAssertThrowsError(try Envelope.open(key: k.down, pairId: pid, direction: "up", path: "/v1/hello", body: body))
        XCTAssertThrowsError(try Envelope.open(key: k.down, pairId: "PAIR-other", direction: "down", path: "/v1/hello", body: body))
    }

    func testRoundTrip() throws {
        let k = Envelope.keys(secret: Data(repeating: 7, count: 32), pairId: "PAIR-x")
        let body = try Envelope.seal(key: k.up, pairId: "PAIR-x", direction: "up", path: "/v1/sync",
                                     payload: ["seq": Int64(3), "entries": [Any](), "v": 1.5, "ok": true])
        let back = try Envelope.open(key: k.up, pairId: "PAIR-x", direction: "up", path: "/v1/sync", body: body)
        XCTAssertEqual(JSON.int(back["seq"]), 3)
        XCTAssertNil(JSON.int(back["v"]))
        XCTAssertTrue(JSON.isBool(back["ok"]))
        XCTAssertNil(JSON.int(back["ok"]))
    }

    func testPairing() {
        for c in Self.vectors["pairing"] as! [[String: Any]] {
            let uri = c["uri"] as! String
            Pairing.allowTestNet = false
            if c["valid"] as! Bool {
                let p = try? Pairing.parse(uri)
                XCTAssertNotNil(p, uri)
                XCTAssertEqual(p?.host, c["host"] as? String)
                XCTAssertEqual(p?.port, c["port"] as? Int)
                XCTAssertEqual(p?.pairId, c["pair_id"] as? String)
                XCTAssertEqual(p?.secret.count, 32)
            } else {
                XCTAssertThrowsError(try Pairing.parse(uri), uri)
                if c["valid_in_test_net"] as? Bool == true {
                    Pairing.allowTestNet = true
                    XCTAssertNoThrow(try Pairing.parse(uri))
                    Pairing.allowTestNet = false
                }
            }
        }
    }

    /// "2026-10-10T11:00-04:00" / "...Z" -> (Date, fixed-offset zone), as Java's ZonedDateTime.parse sees it.
    func parseNow(_ s: String) -> (Date, TimeZone) {
        let base = String(s.prefix(16)), zone = String(s.dropFirst(16))
        var offset = 0
        if zone != "Z" {
            let hm = zone.dropFirst().split(separator: ":").map { Int($0)! }
            offset = (zone.hasPrefix("-") ? -1 : 1) * (hm[0] * 3600 + hm[1] * 60)
        }
        let tz = TimeZone(secondsFromGMT: offset)!
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        let p = base.split(whereSeparator: { "-T:".contains($0) }).map { Int($0)! }
        return (cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2], hour: p[3], minute: p[4]))!, tz)
    }

    func testDosePromptsMatchJavaCore() {
        let r = Self.vectors["rules"] as! [String: Any]
        let rules = Rules(bundle: r["bundle"] as! [String: Any])
        let cases = r["dose_cases"] as! [[String: Any]]
        let expected = r["dose_expected"] as! [[String: Any]]
        XCTAssertEqual(cases.count, expected.count)
        for (c, e) in zip(cases, expected) {
            let (now, tz) = parseNow(c["now"] as! String)
            let answered = Set(c["answered"] as! [String])
            let doses = c["doses"] as! [String: Any]
            let hr = (c["hr"] as! [[Any]]).map { Rules.HR(t: JSON.int($0[0])!, bpm: JSON.number($0[1])!) }
            let got = rules.dosePrompts(now: now, timeZone: tz,
                                        answered: { answered.contains("\($0)|\($1)") },
                                        dosesOn: { Int(JSON.number(doses["\($0)@\($1)"]) ?? 0) },
                                        hr: hr)
            let want = e["prompts"] as! [[String: Any]]
            XCTAssertEqual(got.map(\.id), want.map { $0["id"] as! String }, c["name"] as! String)
            XCTAssertEqual(got.map(\.text), want.map { $0["text"] as! String }, c["name"] as! String)
            XCTAssertTrue(got.allSatisfy { $0.safetyLine == rules.safetyLine && !$0.safetyLine.isEmpty })
        }
    }

    func testBloodPressureTextsMatchJavaCore() {
        let r = Self.vectors["rules"] as! [String: Any]
        let rules = Rules(bundle: r["bundle"] as! [String: Any])
        for c in r["bp"] as! [[String: Any]] {
            let got = rules.afterBloodPressure(systolic: JSON.number(c["sys"])!, diastolic: JSON.number(c["dia"])!,
                                               urgentLast24h: c["urgent"] as! [String])
            XCTAssertEqual(got, c["expected"] as? String)
        }
    }

    func testUpcomingNeverClaimsAPattern() {
        let r = Self.vectors["rules"] as! [String: Any]
        let rules = Rules(bundle: r["bundle"] as! [String: Any])
        let (now, tz) = parseNow("2026-10-10T06:00-04:00")
        let up = rules.upcoming(now: now, timeZone: tz, hours: 24, answered: { _, _ in false })
        XCTAssertFalse(up.isEmpty)
        XCTAssertTrue(up.allSatisfy { $0.text.hasPrefix("Did you take your") })
        XCTAssertTrue(up.contains { $0.id == "DOSE|MED-b|2026-10-10T07:30-04:00" })
        XCTAssertFalse(up.contains { $0.medId == "MED-d" })
    }
}
