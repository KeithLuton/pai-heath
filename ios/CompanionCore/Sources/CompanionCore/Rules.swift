import Foundation

/// A calendar day (yyyy-MM-dd), compared as a date.
public struct Day: Comparable, Hashable, CustomStringConvertible {
    public let year: Int, month: Int, day: Int

    public init(year: Int, month: Int, day: Int) { self.year = year; self.month = month; self.day = day }

    public init?(_ s: String) {
        let p = s.prefix(10).split(separator: "-").map { Int($0) }
        guard p.count == 3, let y = p[0], let m = p[1], let d = p[2] else { return nil }
        self.init(year: y, month: m, day: d)
    }

    public init(_ date: Date, in cal: Calendar) {
        let c = cal.dateComponents([.year, .month, .day], from: date)
        self.init(year: c.year!, month: c.month!, day: c.day!)
    }

    public var description: String { String(format: "%04d-%02d-%02d", year, month, day) }

    public static func < (a: Day, b: Day) -> Bool { (a.year, a.month, a.day) < (b.year, b.month, b.day) }

    func adding(_ n: Int, _ cal: Calendar) -> Day {
        let d = cal.date(from: DateComponents(year: year, month: month, day: day, hour: 12))!
        return Day(cal.date(byAdding: .day, value: n, to: d)!, in: cal)
    }
}

/// The fixed rules the phone runs between syncs, from the bundle the PC sends.
/// Mirrors engine/tracker/compute.py and android/.../core/Rules.java: same texts, same timing,
/// so a prompt reads the same whether the phone or the PC raised it.
/// Prompts ask; they never tell anyone to take a dose (§21.4, §28.2).
public struct Rules {
    public struct DosePrompt: Equatable {
        public let id: String, medId: String, due: String, text: String, safetyLine: String
        public let dueDate: Date
        public let options = ["Yes", "No", "Not sure"]
    }

    /// A heart-rate sample: epoch seconds and bpm.
    public struct HR {
        public let t: Int64, bpm: Double
        public init(t: Int64, bpm: Double) { self.t = t; self.bpm = bpm }
    }

    public let bundle: [String: Any]

    public init(bundle: [String: Any]) { self.bundle = bundle }

    public var safetyLine: String { bundle["safety_line"] as? String ?? "" }

    public var urgentSymptoms: [String] { (bundle["urgent_symptoms"] as? [Any])?.compactMap { $0 as? String } ?? [] }

    public var meds: [[String: Any]] { (bundle["meds"] as? [Any])?.compactMap { $0 as? [String: Any] } ?? [] }

    private func rule(_ k: String) -> Int {
        Int(JSON.number((bundle["dose_rule"] as? [String: Any])?[k]) ?? 0)
    }

    private static func mean(_ hr: [HR], _ a: Int64, _ b: Int64) -> Double? {
        let xs = hr.filter { $0.t >= a && $0.t < b }.map(\.bpm)
        return xs.count >= 3 ? xs.reduce(0, +) / Double(xs.count) : nil
    }

    public static func isoMinute(_ d: Date, _ tz: TimeZone) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = tz
        f.dateFormat = "yyyy-MM-dd'T'HH:mmxxx"
        return f.string(from: d)
    }

    /// answered(medId, dueIso) and dosesOn(medId, day) come from what has been logged.
    public func dosePrompts(now: Date, timeZone tz: TimeZone,
                            answered: (String, String) -> Bool,
                            dosesOn: (String, Day) -> Int,
                            hr: [HR]) -> [DosePrompt] {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        let after = rule("prompt_after_min"), pre = rule("pre_min"), ps = rule("post_start_min"), pe = rule("post_end_min")
        let today = Day(now, in: cal)
        var out: [DosePrompt] = []
        for m in meds {
            let sched = (m["schedule"] as? [Any])?.compactMap { $0 as? String } ?? []
            if sched.isEmpty { continue }
            var start = today.adding(-1, cal)
            if let s = (m["start"] as? String).flatMap({ Day($0) }), s > start { start = s }
            let medId = m["med_id"] as? String ?? "", name = m["name"] as? String ?? ""
            let pat = m["pattern"] as? [String: Any] ?? [:]
            let qualified = (pat["state"] as? String) == "QUALIFIED"
            let delta = JSON.number(pat["delta"])
            var d = start
            while d <= today {
                for when in sched {
                    let hm = when.split(separator: ":").compactMap { Int($0) }
                    guard hm.count >= 2,
                          let due = cal.date(from: DateComponents(year: d.year, month: d.month, day: d.day,
                                                                  hour: hm[0], minute: hm[1])) else { continue }
                    if due.addingTimeInterval(Double(after) * 60) > now { continue }
                    let dueIso = Rules.isoMinute(due, tz)
                    if answered(medId, dueIso) || dosesOn(medId, d) >= sched.count { continue }
                    let day = d == today ? "" : " yesterday"
                    var seen = false
                    if qualified, let delta = delta, !hr.isEmpty {
                        let t = Int64(due.timeIntervalSince1970)
                        if let a = Rules.mean(hr, t - Int64(pre) * 60, t),
                           let b = Rules.mean(hr, t + Int64(ps) * 60, t + Int64(pe) * 60) {
                            seen = (b - a) * delta > 0 && abs(b - a) >= abs(delta) / 2
                        }
                    }
                    let text = seen ? "Looks like you may have taken your \(when) dose of \(name)\(day). Log it?"
                        : qualified ? "Your usual post-dose pattern hasn't shown up after your \(when) dose of \(name)\(day). Did you take it?"
                        : "Did you take your \(when) dose of \(name)\(day)?"
                    out.append(DosePrompt(id: "DOSE|\(medId)|\(dueIso)", medId: medId, due: dueIso, text: text,
                                          safetyLine: safetyLine, dueDate: due))
                }
                d = d.adding(1, cal)
            }
        }
        return out
    }

    /// Upcoming due times (not yet promptable), for notifications scheduled ahead.
    /// Texts are the plain question only: a heart-rate pattern can't be checked before it happens.
    public func upcoming(now: Date, timeZone tz: TimeZone, hours: Int,
                         answered: (String, String) -> Bool,
                         dosesOn: (String, Day) -> Int = { _, _ in 0 }) -> [DosePrompt] {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        let after = Double(rule("prompt_after_min")) * 60
        let today = Day(now, in: cal), end = now.addingTimeInterval(Double(hours) * 3600)
        var out: [DosePrompt] = []
        for m in meds {
            let sched = (m["schedule"] as? [Any])?.compactMap { $0 as? String } ?? []
            let medId = m["med_id"] as? String ?? "", name = m["name"] as? String ?? ""
            let startDay = (m["start"] as? String).flatMap({ Day($0) })
            for offset in 0...((hours / 24) + 1) {
                let d = today.adding(offset, cal)
                if let s = startDay, d < s { continue }
                for when in sched {
                    let hm = when.split(separator: ":").compactMap { Int($0) }
                    guard hm.count >= 2,
                          let due = cal.date(from: DateComponents(year: d.year, month: d.month, day: d.day,
                                                                  hour: hm[0], minute: hm[1])) else { continue }
                    let fire = due.addingTimeInterval(after)
                    guard fire > now, fire <= end else { continue }
                    let dueIso = Rules.isoMinute(due, tz)
                    if answered(medId, dueIso) || dosesOn(medId, d) >= sched.count { continue }
                    let day = Day(fire, in: cal) == d ? "" : " yesterday"
                    out.append(DosePrompt(id: "DOSE|\(medId)|\(dueIso)", medId: medId, due: dueIso,
                                          text: "Did you take your \(when) dose of \(name)\(day)?",
                                          safetyLine: safetyLine, dueDate: due))
                }
            }
        }
        return out
    }

    /// After a blood-pressure reading: the fixed emergency text, the recheck text, or nil.
    public func afterBloodPressure(systolic: Double, diastolic: Double, urgentLast24h: [String]) -> String? {
        let sev = (bundle["bp_severe"] as? [Any])?.compactMap({ JSON.number($0) }) ?? []
        guard sev.count >= 2, systolic >= sev[0] || diastolic >= sev[1] else { return nil }
        if let first = urgentLast24h.first {
            return (bundle["lane3_text"] as? String)?.replacingOccurrences(of: "{symptom}", with: first)
        }
        return bundle["recheck_text"] as? String
    }
}
