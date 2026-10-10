import CryptoKit
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Where the last-used sequence number lives (Keychain/UserDefaults in the app, memory in tests).
public protocol SeqStore: AnyObject {
    var seq: Int64 { get set }
}

public final class MemorySeqStore: SeqStore {
    public var seq: Int64
    public init(_ seq: Int64 = 0) { self.seq = seq }
}

/// Talks to the PC. Every request carries a strictly increasing sequence number; the reply must echo it.
public final class SyncClient {
    public let pairing: Pairing
    private let up: SymmetricKey
    private let down: SymmetricKey
    private let seqStore: SeqStore
    private let device: [String: String]
    private let session: URLSession

    public init(pairing: Pairing, seqStore: SeqStore, device: [String: String], session: URLSession? = nil) {
        self.pairing = pairing
        let k = Envelope.keys(secret: pairing.secret, pairId: pairing.pairId)
        up = k.up
        down = k.down
        self.seqStore = seqStore
        self.device = device
        if let session = session {
            self.session = session
        } else {
            let cfg = URLSessionConfiguration.ephemeral
            cfg.timeoutIntervalForRequest = 30
            cfg.waitsForConnectivity = false
            self.session = URLSession(configuration: cfg)
        }
    }

    public func hello() async throws -> [String: Any] { try await call("/v1/hello", [:]) }

    public func sync(_ payload: [String: Any]) async throws -> [String: Any] { try await call("/v1/sync", payload) }

    public func call(_ path: String, _ payload: [String: Any]) async throws -> [String: Any] {
        let n = seqStore.seq + 1
        var p = payload
        p["seq"] = n
        p["device"] = device
        var req = URLRequest(url: URL(string: pairing.baseURL + path)!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try Envelope.seal(key: up, pairId: pairing.pairId, direction: "up", path: path, payload: p)
        let (data, resp) = try await session.data(for: req)
        seqStore.seq = n                                   // the PC saw this number; never reuse it
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 423 { throw CompanionError.locked }
        guard code == 200 else { throw CompanionError.refused(code) }
        let reply = try Envelope.open(key: down, pairId: pairing.pairId, direction: "down", path: path, body: data)
        guard JSON.int(reply["seq"]) == n else { throw CompanionError.replyMismatch }
        return reply
    }
}
