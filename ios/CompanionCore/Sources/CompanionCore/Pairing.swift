import Foundation

/// What the QR code on the PC carries: paihealth://pair?v=1&h=<ip>&p=<port>&id=<pair id>&k=<secret>
public struct Pairing: Equatable {
    public let host: String
    public let port: Int
    public let pairId: String
    public let secret: Data
    public let uri: String

    /// Tests only: the documentation range 192.0.2.0/24, as the Java core allows in its interop test.
    public static var allowTestNet = false
    /// Tests only (interop CLI): accept whatever address the test machine has.
    public static var allowAnyHostForTests = false

    public static func isPrivateLAN(_ h: String) -> Bool {
        let parts = h.split(separator: ".", omittingEmptySubsequences: false).map { Int($0) }
        guard parts.count == 4, parts.allSatisfy({ $0 != nil && (0...255).contains($0!) }) else { return false }
        let a = parts[0]!, b = parts[1]!
        return a == 10 || (a == 192 && b == 168) || (a == 172 && (16...31).contains(b))
    }

    public static func parse(_ raw: String) throws -> Pairing {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let prefix = "paihealth://pair?"
        guard t.hasPrefix(prefix) else { throw CompanionError.notPairingCode }
        var q: [String: String] = [:]
        for kv in t.dropFirst(prefix.count).split(separator: "&") {
            guard let e = kv.firstIndex(of: "="), e != kv.startIndex else { continue }
            let key = String(kv[kv.startIndex..<e])
            let value = String(kv[kv.index(after: e)...]).replacingOccurrences(of: "+", with: " ")
            q[key] = value.removingPercentEncoding ?? value
        }
        guard q["v"] == "1" else { throw CompanionError.unsupportedVersion }
        guard let h = q["h"] else { throw CompanionError.notPrivateNetwork }
        let testNet = allowTestNet && h.hasPrefix("192.0.2.")
        guard isPrivateLAN(h) || testNet || allowAnyHostForTests else { throw CompanionError.notPrivateNetwork }
        guard let k = q["k"].flatMap({ Envelope.b64d($0) }), k.count == 32 else { throw CompanionError.badPairing("key") }
        guard let p = q["p"].flatMap({ Int($0) }), (1...65535).contains(p) else { throw CompanionError.badPairing("port") }
        guard let id = q["id"], !id.isEmpty else { throw CompanionError.badPairing("id") }
        return Pairing(host: h, port: p, pairId: id, secret: k, uri: t)
    }

    public var baseURL: String { "http://\(host):\(port)" }
}
