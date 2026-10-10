import BackgroundTasks
import CompanionCore
import Foundation
import UserNotifications

/// "Talk to the computer" and "raise check-ins", used by the screen, notification actions and background refresh.
enum PhoneSync {
    static let refreshTask = "com.pailuton.health.companion.refresh"
    static let category = "DOSE"
    static let appVersion = "0.1.0"

    static func device() -> [String: String] {
        var u = utsname()
        uname(&u)
        let machine = withUnsafeBytes(of: &u.machine) { raw in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
        return ["name": "iPhone", "model": machine, "app_version": appVersion]
    }

    static func client() -> SyncClient? {
        guard let uri = AppStore.shared.pairingURI, let p = try? Pairing.parse(uri) else { return nil }
        return SyncClient(pairing: p, seqStore: AppStore.shared, device: device())
    }

    /// Pair: parse the QR text, say hello, keep the pairing and the rules. Throws a readable error.
    static func pair(_ uri: String) async throws {
        let p = try Pairing.parse(uri)
        AppStore.shared.unpair()
        let reply = try await SyncClient(pairing: p, seqStore: AppStore.shared, device: device()).hello()
        AppStore.shared.pairingURI = p.uri
        AppStore.shared.setRules(reply["rules"])
        AppStore.shared.setSync(time: reply["server_time"] as? String, error: nil)
        scheduleRefresh()
        await refreshNotifications()
    }

    /// Send queued entries and new HealthKit data; take back the current rules.
    @discardableResult
    static func sync() async -> String {
        let store = AppStore.shared
        guard let c = client() else { return "Not paired" }
        let queue = store.queuePayload()
        let to = Date()
        let from = store.healthCursor.map { $0.addingTimeInterval(-600) } ?? to.addingTimeInterval(-7 * 86_400)
        var samples: [String: [[Any]]] = [:]
        if store.healthAsked { samples = await HealthReader.shared.read(from: from, to: to) }
        do {
            let reply = try await c.sync(["entries": queue, "samples": samples])
            store.dropSynced(Set(queue.compactMap { $0["uid"] as? String }))
            if store.healthAsked { store.healthCursor = to }
            store.setRules(reply["rules"])
            store.setSync(time: reply["server_time"] as? String, error: nil)
            await refreshNotifications()
            return "Synced: \(JSON.int(reply["accepted"]) ?? 0) new entries"
        } catch CompanionError.locked {
            let m = "PAi Health is locked on the computer. Open it to sync."
            store.setSync(time: nil, error: m)
            return m
        } catch {
            let m = "Couldn't reach the computer (\(error.localizedDescription)). Entries stay on the phone until it can."
            store.setSync(time: nil, error: m)
            await refreshNotifications()
            return m
        }
    }

    // MARK: check-ins

    static func registerCategory() {
        let actions = [UNNotificationAction(identifier: "YES", title: "Yes"),
                       UNNotificationAction(identifier: "NO", title: "No"),
                       UNNotificationAction(identifier: "NOT_SURE", title: "Not sure")]
        UNUserNotificationCenter.current().setNotificationCategories(
            [UNNotificationCategory(identifier: category, actions: actions, intentIdentifiers: [], options: [])])
    }

    static func requestNotifications() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])) ?? false
    }

    private static func content(_ p: Rules.DosePrompt) -> UNMutableNotificationContent {
        let c = UNMutableNotificationContent()
        c.title = "Dose check-in"
        c.body = p.text + "\n\n" + p.safetyLine                       // §21.4, always shown
        c.categoryIdentifier = category
        c.userInfo = ["med_id": p.medId, "due": p.due]
        c.sound = .default
        return c
    }

    /// Raise check-ins that are due now (with the heart-rate pattern, as the computer would word them),
    /// and schedule the next two days ahead so they arrive on time even if the app doesn't run.
    /// Scheduled-ahead texts are the plain question: a pattern can't be checked before it happens.
    static func refreshNotifications() async {
        let store = AppStore.shared
        guard let rules = store.rules else { return }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
        let now = Date(), tz = TimeZone.current
        var hr: [Rules.HR] = []
        if store.healthAsked { hr = await HealthReader.shared.heartRate(from: now.addingTimeInterval(-86_400), to: now) }
        let posted = store.posted
        let delivered = Set(await center.deliveredNotifications().map(\.request.identifier))
        var shown: [String] = []
        for p in rules.dosePrompts(now: now, timeZone: tz, answered: store.answered, dosesOn: store.dosesOn, hr: hr)
        where !posted.contains(p.id) && !delivered.contains(p.id) {
            try? await center.add(UNNotificationRequest(identifier: p.id, content: content(p), trigger: nil))
            shown.append(p.id)
        }
        store.markPosted(shown + delivered.filter { $0.hasPrefix("DOSE|") })
        // Scheduled ahead: keep what is still wanted, drop what isn't (answered, rules changed), add what's new.
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        let after = TimeInterval(JSON.number((rules.bundle["dose_rule"] as? [String: Any])?["prompt_after_min"]) ?? 0) * 60
        let wanted = rules.upcoming(now: now, timeZone: tz, hours: 48, answered: store.answered, dosesOn: store.dosesOn)
            .filter { !posted.contains($0.id) }.prefix(60)
        let wantedIds = Set(wanted.map(\.id))
        let pending = Set(await center.pendingNotificationRequests().map(\.identifier).filter { $0.hasPrefix("DOSE|") })
        center.removePendingNotificationRequests(withIdentifiers: Array(pending.subtracting(wantedIds)))
        for p in wanted where !pending.contains(p.id) {
            let comps = cal.dateComponents([.year, .month, .day, .hour, .minute], from: p.dueDate.addingTimeInterval(after))
            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
            try? await center.add(UNNotificationRequest(identifier: p.id, content: content(p), trigger: trigger))
        }
    }

    /// Yes / No / Not sure on a check-in: logged by you, queued for the computer. Nothing is auto-logged.
    static func answer(_ action: String, userInfo: [AnyHashable: Any]) async {
        guard ["YES", "NO", "NOT_SURE"].contains(action), let med = userInfo["med_id"] as? String else { return }
        let due = userInfo["due"] as? String
        AppStore.shared.enqueue(["type": "DOSE", "med_id": med, "due": due.map { $0 as Any } ?? NSNull(), "taken": action,
                                 "at": action == "YES" && due != nil ? due! : Rules.isoMinute(Date(), .current)])
        await sync()
    }

    // MARK: background

    static func scheduleRefresh() {
        let req = BGAppRefreshTaskRequest(identifier: refreshTask)
        req.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(req)
    }

    static func handle(_ task: BGAppRefreshTask) {
        scheduleRefresh()
        let work = Task {
            await sync()
            task.setTaskCompleted(success: true)
        }
        task.expirationHandler = { work.cancel() }
    }
}
