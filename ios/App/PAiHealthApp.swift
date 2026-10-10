import BackgroundTasks
import SwiftUI
import UIKit
import UserNotifications

@main
struct PAiHealthApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @Environment(\.scenePhase) private var phase
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(model)
        }
        .onChange(of: phase) { p in
            if p == .active { Task { await model.syncNow(quiet: true) } }
            if p == .background { PhoneSync.scheduleRefresh() }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        PhoneSync.registerCategory()
        BGTaskScheduler.shared.register(forTaskWithIdentifier: PhoneSync.refreshTask, using: nil) { task in
            guard let t = task as? BGAppRefreshTask else { task.setTaskCompleted(success: false); return }
            PhoneSync.handle(t)
        }
        return true
    }

    // Show check-ins while the app is open, too.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let action = response.actionIdentifier
        let info = response.notification.request.content.userInfo
        Task {
            await PhoneSync.answer(action, userInfo: info)
            completionHandler()
        }
    }
}

/// What the screen shows; refreshed after every action.
@MainActor
final class AppModel: ObservableObject {
    @Published var paired = AppStore.shared.paired
    @Published var lastSync = AppStore.shared.lastSync
    @Published var lastError = AppStore.shared.lastError
    @Published var waiting = AppStore.shared.queueCount
    @Published var message: String?
    @Published var urgent: String?
    @Published var busy = false

    func refresh() {
        let s = AppStore.shared
        paired = s.paired
        lastSync = s.lastSync
        lastError = s.lastError
        waiting = s.queueCount
    }

    func pair(_ uri: String) async {
        busy = true
        defer { busy = false; refresh() }
        do {
            try await PhoneSync.pair(uri)
            _ = await PhoneSync.requestNotifications()
            await PhoneSync.refreshNotifications()
            message = "Paired."
        } catch {
            message = "Couldn't pair: \(error.localizedDescription)"
        }
    }

    func syncNow(quiet: Bool = false) async {
        guard AppStore.shared.paired else { return }
        busy = true
        let m = await PhoneSync.sync()
        busy = false
        if !quiet { message = m }
        refresh()
    }

    func log(_ entry: [String: Any], urgentText: String? = nil) {
        AppStore.shared.enqueue(entry)
        if let u = urgentText { urgent = u } else { message = "Logged." }
        refresh()
        Task { await syncNow(quiet: true) }
    }

    func unpair() {
        AppStore.shared.unpair()
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        refresh()
    }
}
