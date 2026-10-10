import CryptoKit
import CoreFoundation
import Foundation

/// Errors a person may see; every message says what to do.
public enum CompanionError: Error, Equatable, LocalizedError {
    case notPairingCode
    case unsupportedVersion
    case notPrivateNetwork
    case badPairing(String)
    case wrongEnvelope
    case authenticationFailed
    case malformed
    case locked
    case refused(Int)
    case replyMismatch

    public var errorDescription: String? {
        switch self {
        case .notPairingCode: return "That isn't a PAi Health pairing code."
        case .unsupportedVersion: return "This pairing code is from a newer PAi Health. Update the app."
        case .notPrivateNetwork: return "The computer must be on a private home network."
        case .badPairing(let why): return "The pairing code is damaged (\(why)). Show a new one on the computer."
        case .wrongEnvelope: return "The computer answered for a different pairing."
        case .authenticationFailed: return "The reply couldn't be verified. Pair again."
        case .malformed: return "The computer sent something unreadable."
        case .locked: return "PAi Health is locked on the computer. Open it to sync."
        case .refused(let code): return "The computer refused the sync (\(code))."
        case .replyMismatch: return "The reply doesn't match the request."
        }
    }
}

/// Companion protocol v1 — the same envelope as engine/companion/protocol.py.
///
/// keys:     HKDF-SHA256(secret, salt = pair id, info = "paihealth/companion/v1/up" | ".../down"), 32 bytes
/// envelope: {"v":1,"id":<pair id>,"n":<b64url nonce>,"c":<b64url AES-256-GCM ciphertext+tag>}
/// AAD:      "paihealth/v1|<pair id>|<up|down>|<path>"
public enum Envelope {
    public static let version = 1

    public static func b64e(_ d: Data) -> String {
        d.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func b64d(_ s: String) -> Data? {
        var t = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while t.count % 4 != 0 { t += "=" }
        return Data(base64Encoded: t)
    }

    public static func keys(secret: Data, pairId: String) -> (up: SymmetricKey, down: SymmetricKey) {
        func k(_ info: String) -> SymmetricKey {
            HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: secret), salt: Data(pairId.utf8),
                                   info: Data(info.utf8), outputByteCount: 32)
        }
        return (k("paihealth/companion/v1/up"), k("paihealth/companion/v1/down"))
    }

    public static func aad(pairId: String, direction: String, path: String) -> Data {
        Data("paihealth/v\(version)|\(pairId)|\(direction)|\(path)".utf8)
    }

    /// AES-256-GCM with a given nonce; returns ciphertext followed by the 16-byte tag (the PC's layout).
    public static func sealRaw(key: SymmetricKey, nonce: Data, plaintext: Data, aad: Data) throws -> Data {
        let box = try AES.GCM.seal(plaintext, using: key, nonce: AES.GCM.Nonce(data: nonce), authenticating: aad)
        return box.ciphertext + box.tag
    }

    public static func openRaw(key: SymmetricKey, nonce: Data, combined: Data, aad: Data) throws -> Data {
        guard combined.count >= 16 else { throw CompanionError.malformed }
        do {
            let box = try AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: nonce),
                                            ciphertext: combined.prefix(combined.count - 16),
                                            tag: combined.suffix(16))
            return try AES.GCM.open(box, using: key, authenticating: aad)
        } catch let e as CompanionError {
            throw e
        } catch {
            throw CompanionError.authenticationFailed
        }
    }

    public static func seal(key: SymmetricKey, pairId: String, direction: String, path: String,
                            payload: [String: Any]) throws -> Data {
        let nonce = AES.GCM.Nonce().withUnsafeBytes { Data($0) }        // 12 random bytes
        let plain = try JSON.encode(payload)
        let ct = try sealRaw(key: key, nonce: nonce, plaintext: plain,
                             aad: aad(pairId: pairId, direction: direction, path: path))
        return try JSON.encode(["v": version, "id": pairId, "n": b64e(nonce), "c": b64e(ct)])
    }

    public static func open(key: SymmetricKey, pairId: String, direction: String, path: String,
                            body: Data) throws -> [String: Any] {
        guard let env = try? JSON.object(body) else { throw CompanionError.malformed }
        guard JSON.int(env["v"]) == Int64(version), env["id"] as? String == pairId else {
            throw CompanionError.wrongEnvelope
        }
        guard let n = (env["n"] as? String).flatMap({ b64d($0) }), n.count == 12,
              let c = (env["c"] as? String).flatMap({ b64d($0) }) else { throw CompanionError.malformed }
        let plain = try openRaw(key: key, nonce: n, combined: c,
                                aad: aad(pairId: pairId, direction: direction, path: path))
        guard let out = try? JSON.object(plain) else { throw CompanionError.malformed }
        return out
    }
}

/// JSON with the distinctions the protocol needs (integer vs float vs boolean).
public enum JSON {
    public static func encode(_ obj: Any) throws -> Data {
        try JSONSerialization.data(withJSONObject: obj, options: [.withoutEscapingSlashes, .sortedKeys])
    }

    public static func object(_ data: Data) throws -> [String: Any] {
        guard let o = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) as? [String: Any] else {
            throw CompanionError.malformed
        }
        return o
    }

    public static func isBool(_ v: Any?) -> Bool {
        guard let n = v as? NSNumber else { return false }
        return CFGetTypeID(n) == CFBooleanGetTypeID()
    }

    /// An integer (not a boolean, not a fraction), or nil.
    public static func int(_ v: Any?) -> Int64? {
        guard let n = v as? NSNumber, !isBool(n), !String(cString: n.objCType).contains("f") && !String(cString: n.objCType).contains("d") else { return nil }
        return n.int64Value
    }

    /// A number (integer or fraction, not a boolean), or nil.
    public static func number(_ v: Any?) -> Double? {
        guard let n = v as? NSNumber, !isBool(n) else { return nil }
        return n.doubleValue
    }
}
