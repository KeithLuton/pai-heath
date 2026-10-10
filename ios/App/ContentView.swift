import CompanionCore
import SwiftUI

/// One screen: pair with the computer, then log a dose, a blood-pressure reading or a symptom.
/// Logs are "logged by you" (§18) and wait on the phone until the computer is reachable.
struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack {
            Group {
                if model.paired { HomeView() } else { PairView() }
            }
            .navigationTitle("PAi Health")
            .alert(model.urgent ?? "", isPresented: Binding(get: { model.urgent != nil }, set: { if !$0 { model.urgent = nil } })) {
                Button("Call 911") { openURL(URL(string: "tel://911")!) }        // §28.3 fixed text, with a way to dial
                Button("Close", role: .cancel) {}
            }
        }
        .alert(model.message ?? "", isPresented: Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })) {
            Button("OK", role: .cancel) {}
        }
    }
}

struct PairView: View {
    @EnvironmentObject var model: AppModel
    @State private var scanning = false
    @State private var manual = ""

    var body: some View {
        Form {
            Section {
                Text("On your computer: PAi Health › Settings › Phone companion › Turn on › Pair a phone. Then scan the code it shows.")
                Button("Scan the pairing code") { scanning = true }
                    .disabled(model.busy)
            }
            Section("Or paste the pairing link") {
                TextField("paihealth://pair?…", text: $manual)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Pair") { Task { await model.pair(manual) } }
                    .disabled(manual.isEmpty || model.busy)
            }
            Section {
                Text("Your phone and computer must be on the same home Wi-Fi. iPhone will ask to find devices on your local network — allow it.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .sheet(isPresented: $scanning) {
            QRScannerView { code in
                scanning = false
                Task { await model.pair(code) }
            }
            .ignoresSafeArea()
        }
    }
}

struct HomeView: View {
    @EnvironmentObject var model: AppModel
    @State private var showBP = false
    @State private var showSymptom = false
    @State private var confirmUnpair = false
    @State private var healthAsked = AppStore.shared.healthAsked

    private var rules: Rules? { AppStore.shared.rules }

    var body: some View {
        Form {
            Section {
                Text("Paired with your computer. Last sync: \(model.lastSync ?? "never")").font(.subheadline)
                if let e = model.lastError { Text(e).font(.subheadline).foregroundStyle(.orange) }
                if model.waiting > 0 {
                    Text("\(model.waiting) entr\(model.waiting == 1 ? "y" : "ies") waiting to sync").font(.subheadline)
                }
                Button(model.busy ? "Syncing…" : "Sync now") { Task { await model.syncNow() } }
                    .disabled(model.busy)
            }
            if HealthReader.shared.available && !healthAsked {
                Section {
                    Button("Allow reading Apple Health") {
                        Task {
                            try? await HealthReader.shared.requestAccess()
                            AppStore.shared.healthAsked = true
                            healthAsked = true
                            await model.syncNow(quiet: true)
                        }
                    }
                    Text("Read only: heart rate, resting heart rate, steps, sleep, blood pressure, weight, glucose, oxygen. Sent only to your computer.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let r = rules {
                Section("Log") {
                    ForEach(Array(r.meds.enumerated()), id: \.offset) { _, m in
                        if let name = m["name"] as? String, let med = m["med_id"] as? String {
                            Button("I took \(name) now") {
                                model.log(["type": "DOSE", "med_id": med, "due": NSNull(), "taken": "YES",
                                           "at": Rules.isoMinute(Date(), .current)])
                            }
                        }
                    }
                    Button("Blood pressure reading") { showBP = true }
                    Button("Symptom") { showSymptom = true }
                }
            }
            Section {
                Text("PAi Health keeps your readings on your phone and your own computer only. No cloud account, no cloud copy, no analytics, no advertising.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Unpair this phone", role: .destructive) { confirmUnpair = true }
            }
        }
        .refreshable { await model.syncNow() }
        .sheet(isPresented: $showBP) { if let r = rules { BloodPressureSheet(rules: r).environmentObject(model) } }
        .sheet(isPresented: $showSymptom) { if let r = rules { SymptomSheet(rules: r).environmentObject(model) } }
        .confirmationDialog("Unpair? Entries not yet synced will be lost.", isPresented: $confirmUnpair, titleVisibility: .visible) {
            Button("Unpair", role: .destructive) { model.unpair() }
        }
    }
}

struct BloodPressureSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let rules: Rules
    @State private var sys = ""
    @State private var dia = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Systolic (top)", text: $sys).keyboardType(.numberPad)
                TextField("Diastolic (bottom)", text: $dia).keyboardType(.numberPad)
                Text("Seated, back supported, 5 minutes rest, arm at heart level.").font(.footnote).foregroundStyle(.secondary)
            }
            .navigationTitle("Blood pressure")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Log") {
                        guard let s = Double(sys), let d = Double(dia), d < s else { return }
                        let msg = rules.afterBloodPressure(systolic: s, diastolic: d, urgentLast24h: AppStore.shared.recentUrgent())
                        model.log(["type": "BP", "systolic": s, "diastolic": d, "at": Rules.isoMinute(Date(), .current)],
                                  urgentText: msg)
                        dismiss()
                    }
                    .disabled(Double(sys) == nil || Double(dia) == nil)
                }
            }
        }
    }
}

struct SymptomSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let rules: Rules
    @State private var what = ""
    @State private var picked: Set<String> = []

    var body: some View {
        NavigationStack {
            Form {
                TextField("What you noticed", text: $what)
                Section("Any of these right now?") {
                    ForEach(rules.urgentSymptoms, id: \.self) { s in
                        Toggle(s, isOn: Binding(get: { picked.contains(s) },
                                                set: { if $0 { picked.insert(s) } else { picked.remove(s) } }))
                    }
                }
            }
            .navigationTitle("Symptom")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Log") {
                        let urgent = rules.urgentSymptoms.filter { picked.contains($0) }
                        let text = what.trimmingCharacters(in: .whitespaces).isEmpty ? urgent.joined(separator: ", ") : what
                        guard !text.isEmpty else { return }
                        model.log(["type": "SYMPTOM", "text": text, "urgent": urgent, "at": Rules.isoMinute(Date(), .current)],
                                  urgentText: urgent.isEmpty ? nil : "If this is happening now, call 911 or your local emergency number.")
                        dismiss()
                    }
                }
            }
        }
    }
}
