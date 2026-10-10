import CompanionCore
import Foundation
import Security

/// Everything the phone keeps.
///
/// * The pairing link (it holds the shared secret) lives in the Keychain, this device only.
/// * Everything else — rules from the computer, entries waiting to sync, the sequence counter,
///   which check-ins were answered — lives in one file with iOS Data Protection
///   (unreadable until the phone has been unlocked once after a restart).
final class AppStore: SeqStore, @unchecked Sendable {
    static let shared = AppStore()

    struct Queued: Codable { let uid: String; let entry: String }   // entry: JSON object text

    struct State: Codable {
        var rulesJSON: String?
        var lastSync: String?
        var lastError: String?
        var seq: Int64 = 0
        var queue: [Queued] = []
        var answered: [String: Int] = [:]      // "med|due" -> 1, "med@yyyy-MM-dd" -> doses that day
        var posted: [String] = []              // check-in ids already shown
        var healthCursor: Date?
        var healthAsked = false
    }

    private let lock = NSLock()
    private var state: State
    private let file: URL
    private let keychainAccount = "pairing"
    private let keychainService = "com.pailuton.health.companion"

    private init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PAiHealth", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        file = dir.appendingPathComponent("state.json")
        state = (try? JSONDecoder().decode(State.self, from: Data(contentsOf: file))) ?? State()
    }

    private func mutate(_ f: (inout State) -> Void) {
        lock.lock()
        defer { lock.unlock() }
        f(&state)
        if let d = try? JSONEncoder().encode(state) {
            try? d.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }
    }

    private func read<T>(_ f: (State) -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return f(state)
    }

    // MARK: SeqStore — must never go backwards
    var seq: Int64 {
        get { read { $0.seq } }
        set { mutate { $0.seq = max($0.seq, newValue) } }
    }

    // MARK: pairing (Keychain)
    var pairingURI: String? {
        get {
            var out: AnyObject?
            let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService,
                                    kSecAttrAccount as String: keychainAccount, kSecReturnData as String: true]
            guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
            return String(data: d, encoding: .utf8)
        }
        set {
            let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService,
                                       kSecAttrAccount as String: keychainAccount]
            SecItemDelete(base as CFDictionary)
            guard let v = newValue else { return }
            var add = base
            add[kSecValueData as String] = Data(v.utf8)
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    var paired: Bool { pairingURI != nil }

    var rules: Rules? {
        read { $0.rulesJSON }.map({ Data($0.utf8) }).flatMap({ try? JSON.object($0) }).map({ Rules(bundle: $0) })
    }

    func setRules(_ bundle: Any?) {
        guard let b = bundle, let d = try? JSON.encode(b) else { return }
        mutate { $0.rulesJSON = String(data: d, encoding: .utf8) }
    }

    var lastSync: String? { read { $0.lastSync } }
    var lastError: String? { read { $0.lastError } }
    func setSync(time: String?, error: String?) { mutate { if let t = time { $0.lastSync = t }; $0.lastError = error } }

    var healthCursor: Date? {
        get { read { $0.healthCursor } }
        set { mutate { $0.healthCursor = newValue } }
    }

    var healthAsked: Bool {
        get { read { $0.healthAsked } }
        set { mutate { $0.healthAsked = newValue } }
    }

    // MARK: entries waiting to sync
    var queueCount: Int { read { $0.queue.count } }

    func queuePayload() -> [[String: Any]] {
        read { s in s.queue.compactMap { q in
            (try? JSON.object(Data(q.entry.utf8))).map { ["uid": q.uid, "entry": $0] }
        } }
    }

    func queuedEntries() -> [[String: Any]] {
        read { s in s.queue.compactMap { try? JSON.object(Data($0.entry.utf8)) } }
    }

    func enqueue(_ entry: [String: Any]) {
        guard let d = try? JSON.encode(entry), let text = String(data: d, encoding: .utf8) else { return }
        mutate { s in
            s.queue.append(Queued(uid: UUID().uuidString, entry: text))
            if entry["type"] as? String == "DOSE", let med = entry["med_id"] as? String, let at = entry["at"] as? String {
                if let due = entry["due"] as? String { s.answered["\(med)|\(due)"] = 1 }
                s.answered["\(med)@\(at.prefix(10))", default: 0] += 1
            }
        }
    }

    func dropSynced(_ uids: Set<String>) { mutate { $0.queue.removeAll { uids.contains($0.uid) } } }

    // MARK: check-ins
    func answered(_ med: String, _ due: String) -> Bool { read { $0.answered["\(med)|\(due)"] != nil } }
    func dosesOn(_ med: String, _ day: Day) -> Int { read { $0.answered["\(med)@\(day)"] ?? 0 } }
    var posted: Set<String> { read { Set($0.posted) } }
    func markPosted(_ ids: [String]) { mutate { $0.posted = Array(($0.posted + ids).suffix(300)) } }

    /// Urgent symptoms logged in the last 24 hours (for the fixed blood-pressure texts).
    func recentUrgent(now: Date = Date()) -> [String] {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mmxxx"
        return queuedEntries()
            .filter { $0["type"] as? String == "SYMPTOM" }
            .filter { ($0["at"] as? String).flatMap({ f.date(from: $0) }).map({ now.timeIntervalSince($0) < 86_400 }) ?? false }
            .flatMap { ($0["urgent"] as? [Any])?.compactMap({ $0 as? String }) ?? [] }
    }

    func unpair() {
        pairingURI = nil
        mutate { $0 = State() }
    }
}
