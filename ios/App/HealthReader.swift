import CompanionCore
import Foundation
import HealthKit

/// Reads HealthKit into the PC's row format (same as the Android app and the PC's Apple Health importer):
/// [start, end, value, unit, source, "healthkit", device_class, method, category, conversion, raw_type, time_basis]
/// Read only. Values are passed as recorded, in the PC's units (bpm, count, mmHg, kg, mg/dL, %).
final class HealthReader: @unchecked Sendable {
    static let shared = HealthReader()
    let store = HKHealthStore()

    var available: Bool { HKHealthStore.isHealthDataAvailable() }

    private struct Q { let measure: String; let id: HKQuantityTypeIdentifier; let unit: HKUnit; let scale: Double; let unitLabel: String }

    private let quantities: [Q] = [
        Q(measure: "heart_rate", id: .heartRate, unit: HKUnit.count().unitDivided(by: .minute()), scale: 1, unitLabel: "bpm"),
        Q(measure: "resting_heart_rate", id: .restingHeartRate, unit: HKUnit.count().unitDivided(by: .minute()), scale: 1, unitLabel: "bpm"),
        Q(measure: "steps", id: .stepCount, unit: .count(), scale: 1, unitLabel: "count"),
        Q(measure: "bp_systolic", id: .bloodPressureSystolic, unit: .millimeterOfMercury(), scale: 1, unitLabel: "mmHg"),
        Q(measure: "bp_diastolic", id: .bloodPressureDiastolic, unit: .millimeterOfMercury(), scale: 1, unitLabel: "mmHg"),
        Q(measure: "body_mass", id: .bodyMass, unit: .gramUnit(with: .kilo), scale: 1, unitLabel: "kg"),
        Q(measure: "glucose", id: .bloodGlucose, unit: HKUnit(from: "mg/dL"), scale: 1, unitLabel: "mg/dL"),
        Q(measure: "spo2", id: .oxygenSaturation, unit: .percent(), scale: 100, unitLabel: "%"),
    ]

    var readTypes: Set<HKObjectType> {
        var s = Set<HKObjectType>(quantities.map { HKQuantityType($0.id) })
        s.insert(HKCategoryType(.sleepAnalysis))
        return s
    }

    /// iOS never says whether read access was granted (privacy); asking twice is harmless.
    func requestAccess() async throws {
        guard available else { return }
        try await store.requestAuthorization(toShare: [], read: readTypes)
    }

    private func samples(_ type: HKSampleType, from: Date, to: Date) async throws -> [HKSample] {
        try await withCheckedThrowingContinuation { cont in
            let pred = HKQuery.predicateForSamples(withStart: from, end: to, options: [])
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
            let q = HKSampleQuery(sampleType: type, predicate: pred, limit: HKObjectQueryNoLimit,
                                  sortDescriptors: [sort]) { _, results, error in
                if let error = error { cont.resume(throwing: error) } else { cont.resume(returning: results ?? []) }
            }
            store.execute(q)
        }
    }

    private let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = .current
        return f
    }()

    private func deviceClass(_ s: HKSample) -> String {
        let model = (s.device?.model ?? "") + " " + (s.sourceRevision.productType ?? "")
        if model.localizedCaseInsensitiveContains("watch") { return "WATCH" }
        if model.localizedCaseInsensitiveContains("iphone") { return "PHONE" }
        return "OTHER_SOURCE"
    }

    private func row(_ s: HKSample, _ value: Double?, _ unit: String, _ raw: String, _ category: String? = nil) -> [Any] {
        [iso.string(from: s.startDate), iso.string(from: s.endDate), value.map { $0 as Any } ?? NSNull(), unit,
         s.sourceRevision.source.name, "healthkit", deviceClass(s), NSNull(), category.map { $0 as Any } ?? NSNull(), NSNull(),
         raw, "DEVICE_CLOCK"]
    }

    private static let sleepStage: [Int: String] = [
        HKCategoryValueSleepAnalysis.inBed.rawValue: "IN_BED",
        HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue: "ASLEEP_UNSPECIFIED",
        HKCategoryValueSleepAnalysis.asleepCore.rawValue: "CORE",
        HKCategoryValueSleepAnalysis.asleepDeep.rawValue: "DEEP",
        HKCategoryValueSleepAnalysis.asleepREM.rawValue: "REM",
        HKCategoryValueSleepAnalysis.awake.rawValue: "AWAKE",
    ]

    /// Samples by measure between two times. Types the person didn't allow simply come back empty.
    func read(from: Date, to: Date) async -> [String: [[Any]]] {
        guard available else { return [:] }
        var out: [String: [[Any]]] = [:]
        for q in quantities {
            let type = HKQuantityType(q.id)
            guard let got = try? await samples(type, from: from, to: to) else { continue }
            for case let s as HKQuantitySample in got {
                out[q.measure, default: []].append(row(s, s.quantity.doubleValue(for: q.unit) * q.scale, q.unitLabel, q.id.rawValue))
            }
        }
        if let got = try? await samples(HKCategoryType(.sleepAnalysis), from: from, to: to) {
            for case let s as HKCategorySample in got {
                out["sleep_stage", default: []].append(row(s, nil, "interval", HKCategoryTypeIdentifier.sleepAnalysis.rawValue,
                                                         Self.sleepStage[s.value] ?? "UNKNOWN"))
            }
        }
        return out
    }

    /// Heart rate in a window, for the dose-pattern check.
    func heartRate(from: Date, to: Date) async -> [Rules.HR] {
        guard available, let got = try? await samples(HKQuantityType(.heartRate), from: from, to: to) else { return [] }
        let bpm = HKUnit.count().unitDivided(by: .minute())
        return got.compactMap { ($0 as? HKQuantitySample).map { Rules.HR(t: Int64($0.startDate.timeIntervalSince1970),
                                                                      bpm: $0.quantity.doubleValue(for: bpm)) } }
    }
}
