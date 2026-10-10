"""Tracker engine (Part II): the Tracker contract from the person's own data.

Inputs: first-person logs (§19), device rows (§20), record Floats (Part I), settings (§29).
Every number shown is computed here from those inputs; nothing is invented to
fill a page. Where data is missing the output says what is missing.

Rules applied: §21 dose patterns and prompts, §22 change windows, §24 lanes,
§26 blood-pressure suggestions and gates, §27 annual labs and hold list,
§25.5 interaction checks, §28 notification boundary (fixed safety texts,
rule ids recorded on every lane and prompt).
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from . import library as L

LOW_AUTHORITY_BP = {"WATCH", "WATCH_OR_TRACKER", "PHONE"}       # §24.3 rule 3
TITLES = {s["id"]: s["title"] for s in L.BP_SUGGESTIONS}


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _r(x: float, n: int = 0):
    return round(x, n) if n else int(round(x))


# ---------------------------------------------------------------- inputs
class Inputs:
    def __init__(self, logs: List[dict], rows: Callable[[str], List[list]], floats: List[dict], settings: dict,
                 user_name: str, now: datetime):
        self.logs, self._rows, self.floats, self.settings, self.user_name = logs, rows, floats, settings, user_name
        self.now = now
        self.today = now.date()
        self._cache: Dict[str, List[list]] = {}

    def rows(self, measure: str) -> List[list]:
        if measure not in self._cache:
            self._cache[measure] = self._rows(measure)
        return self._cache[measure]

    def entries(self, t: str) -> List[dict]:
        return [e for e in self.logs if e["type"] == t]

    @property
    def profile(self) -> dict:
        p = self.entries("PROFILE")
        return p[-1] if p else {}


# ---------------------------------------------------------------- series
def bp_readings(I: Inputs, since: date) -> List[dict]:
    out = []
    for e in I.entries("BP"):
        t = _dt(e["at"])
        if t.date() >= since:
            out.append({"t": t, "sys": e["systolic"], "dia": e["diastolic"], "device_class": "HOME_CUFF",
                        "standing": "FIRST_PERSON", "source": "logged by you"})
    dia = {(r[0], r[4]): r for r in I.rows("bp_diastolic")}
    for r in I.rows("bp_systolic"):
        t = _dt(r[0])
        d = dia.get((r[0], r[4]))
        if d and t.date() >= since:
            out.append({"t": t, "sys": r[2], "dia": d[2], "device_class": r[6], "standing": "DEVICE", "source": r[4]})
    out.sort(key=lambda x: x["t"])
    return out


def daily_sum_primary(rows: List[list], since: date) -> Dict[date, float]:
    """Cumulative measures: per day, the single source with the largest total (no double counting phone + watch)."""
    per: Dict[date, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        d = _dt(r[0]).date()
        if d >= since and r[2] is not None:
            per[d][r[4]] += r[2]
    return {d: max(v.values()) for d, v in per.items()}


def daily_mean(rows: List[list], since: date) -> Dict[date, float]:
    per: Dict[date, List[float]] = defaultdict(list)
    for r in rows:
        d = _dt(r[0]).date()
        if d >= since and r[2] is not None:
            per[d].append(r[2])
    return {d: st.fmean(v) for d, v in per.items()}


def sleep_hours(rows: List[list], since: date) -> Dict[date, float]:
    asleep = {"CORE", "DEEP", "REM", "LIGHT", "ASLEEP_UNSPECIFIED", "RESTLESS"}
    per: Dict[date, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r[8] not in asleep:
            continue
        a, b = _dt(r[0]), _dt(r[1])
        if b.date() >= since:
            per[b.date()][r[4]] += (b - a).total_seconds() / 3600
    return {d: max(v.values()) for d, v in per.items()}


# ---------------------------------------------------------------- medications
def _norm_name(s: str) -> str:
    return " ".join(s.lower().split())


def medications_in_use(I: Inputs) -> List[dict]:
    meds = [dict(e, standing="FIRST_PERSON", origin="LOG") for e in I.entries("MEDICATION") if not e.get("stopped")]
    logged = {_norm_name(m["name"]).split()[0] for m in meds}
    seen = set()
    for f in I.floats:
        if f["kind"] != "MEDICATION_STATUS" or f["polarity"] != "AFFIRMED":
            continue
        med = f.get("medication") or {}
        name = med.get("drug") or f["concept"]
        key = _norm_name(name).split()[0]
        if key in logged or key in seen or med.get("med_status") in ("STOPPED", "DISCONTINUED"):
            continue
        seen.add(key)
        sd = med.get("start_date")
        meds.append({"type": "MEDICATION", "med_id": "REC-" + f["id"], "name": name, "dose": med.get("dose_text") or "",
                     "schedule": [], "start": sd if sd and sd != "UNKNOWN" and len(sd) == 10 else None,
                     "targets": [], "sensor_class": None, "standing": "CLINICAL", "origin": "RECORD",
                     "source_float": f["id"]})
    return meds


def med_names(meds: List[dict]) -> set:
    return {w for m in meds for w in _norm_name(m["name"]).replace("/", " ").split()}


def _dose_logs(I: Inputs, med_id: str) -> List[dict]:
    return [e for e in I.entries("DOSE") if e["med_id"] == med_id]


def _due_times(m: dict, start: date, end: date, tz) -> List[datetime]:
    out, d = [], start
    while d <= end:
        for s in m.get("schedule", []):
            hh, mm = map(int, s.split(":"))
            out.append(datetime.combine(d, time(hh, mm), tz))
        d += timedelta(days=1)
    return out


def adherence(I: Inputs, m: dict, days: int = 14) -> Tuple[str, Optional[float]]:
    if not m.get("schedule"):
        return ("No schedule set — doses can still be logged" if m["origin"] == "LOG"
                else "Listed in your records; add it to track doses"), None
    since = I.today - timedelta(days=days - 1)
    if m.get("start"):
        since = max(since, date.fromisoformat(m["start"]))
    due = [t for t in _due_times(m, since, I.today, I.now.tzinfo) if t <= I.now]
    logs = _dose_logs(I, m["med_id"])
    yes = sum(1 for e in logs if e["taken"] == "YES" and _dt(e["at"]).date() >= since)
    no = sum(1 for e in logs if e["taken"] == "NO" and _dt(e["at"]).date() >= since)
    if not due:
        return "No doses due yet", None
    unlogged = max(0, len(due) - yes - no)
    return (f"{yes} of {len(due)} scheduled doses logged as taken in the last {(I.today - since).days + 1} days"
            + (f"; {unlogged} not logged" if unlogged else "") + (f"; {no} logged as not taken" if no else "")), \
        yes / len(due)


def _window_mean(hr: List[Tuple[datetime, float]], a: datetime, b: datetime) -> Optional[float]:
    v = [x for t, x in hr if a <= t < b]
    return st.fmean(v) if len(v) >= 3 else None


def dose_pattern(I: Inputs, m: dict) -> dict:
    """§21.1–21.2: learn the person's own post-dose heart-rate response; qualify it or say there isn't one."""
    R = L.DOSE_RULE
    if not m.get("schedule"):
        return {"state": "NOT_DETECTABLE", "text": "Needs a dose schedule before any pattern can be learned.", "delta": None}
    hr = [(_dt(r[0]), r[2]) for r in I.rows("heart_rate") if r[2] is not None]
    if not hr:
        return {"state": "LEARNING", "text": "No heart-rate data yet; reminders use your schedule only.", "delta": None}

    def delta(t: datetime) -> Optional[float]:
        pre = _window_mean(hr, t - timedelta(minutes=R["pre_min"]), t)
        post = _window_mean(hr, t + timedelta(minutes=R["post_start_min"]), t + timedelta(minutes=R["post_end_min"]))
        return None if pre is None or post is None else post - pre

    dose_d = [d for d in (delta(_dt(e["at"])) for e in _dose_logs(I, m["med_id"]) if e["taken"] == "YES") if d is not None]
    comp = []
    for e in _dose_logs(I, m["med_id"]):
        if e["taken"] == "NO" and e.get("due"):
            comp.append(delta(_dt(e["due"])))
    if m.get("start"):
        s = date.fromisoformat(m["start"])
        for t in _due_times(m, s - timedelta(days=30), s - timedelta(days=1), I.now.tzinfo):
            comp.append(delta(t))
    comp = [d for d in comp if d is not None]
    if len(dose_d) < R["min_dose_days"] or len(comp) < R["min_comparison_days"]:
        return {"state": "LEARNING", "delta": None,
                "text": f"Learning: {len(dose_d)} of {R['min_dose_days']} dose days and {len(comp)} of "
                        f"{R['min_comparison_days']} comparison days with heart-rate data around the dose time."}
    md, mc = st.fmean(dose_d), st.fmean(comp)
    sd = st.pstdev(dose_d + comp) or 1.0
    diff = md - mc
    if abs(diff) >= R["min_effect_bpm"] and abs(diff) / sd >= R["min_separation"]:
        word = "falls" if diff < 0 else "rises"
        return {"state": "QUALIFIED", "delta": diff,
                "text": f"On dose days your heart rate {word} about {abs(_r(diff))} bpm within "
                        f"{R['post_end_min'] // 60} hours of a logged dose ({len(dose_d)} dose days, "
                        f"{len(comp)} comparison days). Missed-dose checks use this."}
    return {"state": "NOT_DETECTABLE", "delta": None,
            "text": "No reliable pattern found for this medication; reminders will use your schedule only."}


def dose_prompts(I: Inputs, meds: List[dict], patterns: Dict[str, dict]) -> List[dict]:
    """§21.3 in review mode: scheduled doses from yesterday and today with no log."""
    R = L.DOSE_RULE
    out = []
    hr = [(_dt(r[0]), r[2]) for r in I.rows("heart_rate") if r[2] is not None]
    for m in meds:
        if m["origin"] != "LOG" or not m.get("schedule"):
            continue
        start = max(I.today - timedelta(days=1), date.fromisoformat(m["start"]) if m.get("start") else I.today - timedelta(days=1))
        logged_due = {e.get("due") for e in _dose_logs(I, m["med_id"])}
        logged_days = defaultdict(int)
        for e in _dose_logs(I, m["med_id"]):
            logged_days[_dt(e["at"]).date()] += 1
        for t in _due_times(m, start, I.today, I.now.tzinfo):
            if t + timedelta(minutes=R["prompt_after_min"]) > I.now or t.isoformat(timespec="minutes") in logged_due:
                continue
            if logged_days[t.date()] >= len(m["schedule"]):
                continue
            when = t.strftime("%H:%M")
            day = "" if t.date() == I.today else " yesterday"
            p = patterns.get(m["med_id"], {})
            seen = False
            if p.get("state") == "QUALIFIED":
                pre = _window_mean(hr, t - timedelta(minutes=R["pre_min"]), t)
                post = _window_mean(hr, t + timedelta(minutes=R["post_start_min"]), t + timedelta(minutes=R["post_end_min"]))
                seen = pre is not None and post is not None and (post - pre) * p["delta"] > 0 and \
                    abs(post - pre) >= abs(p["delta"]) / 2
            text = (f"Looks like you may have taken your {when} dose of {m['name']}{day}. Log it?" if seen else
                    f"Did you take your {when} dose of {m['name']}{day}?" if p.get("state") != "QUALIFIED" else
                    f"Your usual post-dose pattern hasn't shown up after your {when} dose of {m['name']}{day}. Did you take it?")
            out.append({"id": f"DOSE|{m['med_id']}|{t.isoformat(timespec='minutes')}", "kind": "DOSE", "text": text,
                        "options": ["Yes", "No", "Not sure"], "safety_line": L.DOSE_SAFETY,
                        "action": {"type": "DOSE", "med_id": m["med_id"], "due": t.isoformat(timespec="minutes")},
                        "rule": f"{R['id']} v{R['version']}" + (" · found in review" if I.settings.get("data_path", "REVIEW") == "REVIEW" else "")})
    return out


# ---------------------------------------------------------------- lanes (§24)
def urgent_symptoms(I: Inputs, hours: int = 24) -> List[str]:
    cut = I.now - timedelta(hours=hours)
    return [s for e in I.entries("SYMPTOM") if _dt(e["at"]) >= cut for s in e.get("urgent", [])]


def bp_lane(I: Inputs, readings: List[dict]) -> Tuple[dict, List[dict]]:
    R = L.BP_RULE
    since = I.today - timedelta(days=R["window_days"] - 1)
    good = [r for r in readings if r["t"].date() >= since and r["device_class"] not in LOW_AUTHORITY_BP]
    prompts = []
    lane = {"area": "Blood pressure", "lane": None, "rule": f"{R['id']} v{R['version']}", "escalates_if": None,
            "history": "", "summary": ""}
    severe = [r for r in good if r["sys"] >= R["severe"][0] or r["dia"] >= R["severe"][1]]
    recent_severe = [r for r in severe if r["t"] >= I.now - timedelta(hours=24)]
    sym = urgent_symptoms(I)
    if recent_severe and sym:
        lane.update(lane=3, summary=L.LANE3_TEXT.format(symptom=sym[0]), history="", escalates_if=None)
        return lane, prompts
    if not good:
        lane.update(summary="No home blood-pressure readings in the last two weeks.",
                    history="Log readings from a validated upper-arm cuff, or import them, to track this.")
        return lane, prompts
    ms, md = st.fmean(r["sys"] for r in good), st.fmean(r["dia"] for r in good)
    avg = f"{_r(ms)}/{_r(md)}"
    lane["history"] = f"Average {avg} from {len(good)} readings over the last {R['window_days']} days."
    if recent_severe:
        r = recent_severe[-1]
        lane.update(lane=2, summary=f"A reading of {_r(r['sys'])}/{_r(r['dia'])} was logged. Sit quietly for 5 minutes and "
                                     "measure again; if it stays this high, contact your clinician today.")
        prompts.append({"id": f"MEASURE|BP|{r['t'].isoformat(timespec='minutes')}", "kind": "MEASURE",
                        "text": "Recheck your blood pressure after 5 minutes of quiet sitting.", "options": ["Log a reading"],
                        "safety_line": "If you have chest pain, shortness of breath, weakness, trouble speaking or vision changes, call 911.",
                        "action": {"type": "OPEN", "route": "vitals"}, "rule": f"{R['id']} v{R['version']}"})
        return lane, prompts
    if len(good) < R["min_readings"]:
        lane.update(summary=f"{len(good)} readings so far; {R['min_readings']} over {R['window_days']} days are needed before "
                            "the check-in rules apply.", escalates_if=None)
        return lane, prompts
    lane["_elevated"] = ms >= R["lane1"][0] or md >= R["lane1"][1]
    if ms >= R["lane2"][0] or md >= R["lane2"][1]:
        lane.update(lane=2, summary=f"Your home average of {avg} has stayed at or above {R['lane2'][0]}/{R['lane2'][1]}. "
                                     "Keep going with the suggestions, and bring these readings to a clinician visit.",
                    escalates_if=f"any reading reaches {R['severe'][0]}/{R['severe'][1]}, or you log chest pain, shortness "
                                 "of breath, weakness or vision changes")
    elif ms >= R["lane1"][0] or md >= R["lane1"][1]:
        lane.update(lane=1, summary=f"Your home average of {avg} is above {R['lane1'][0]}/{R['lane1'][1]}. Lifestyle "
                                     "suggestions are available.",
                    escalates_if=f"the average stays at or above {R['lane2'][0]}/{R['lane2'][1]}, or the suggestions you "
                                 "follow show no change over their window")
    else:
        lane.update(lane=1, summary=f"Your home average of {avg} is below {R['lane1'][0]}/{R['lane1'][1]}.",
                    escalates_if=f"the average rises to {R['lane2'][0]}/{R['lane2'][1]} or above")
    return lane, prompts


def glucose_lane(I: Inputs) -> dict:
    R = L.GLUCOSE_RULE
    since = I.today - timedelta(days=R["window_days"] - 1)
    fast = [(_dt(e["at"]).date(), e["mg_dl"]) for e in I.entries("GLUCOSE") if e["fasting"] and _dt(e["at"]).date() >= since]
    lane = {"area": "Blood sugar", "lane": None, "rule": f"{R['id']} v{R['version']}", "history": "", "escalates_if": None}
    if not fast:
        lane["summary"] = "No fasting glucose readings in the last 30 days."
        lane["history"] = "Log fasting readings from a home meter to track this."
        return lane
    high_days = {d for d, v in fast if v >= R["lane2_fasting"]}
    mean = st.fmean(v for _, v in fast)
    lane["history"] = f"{len(fast)} fasting readings in 30 days, average {_r(mean)} mg/dL."
    if len(high_days) >= R["lane2_days"]:
        lane.update(lane=2, summary=f"Fasting readings were {R['lane2_fasting']} mg/dL or higher on {len(high_days)} days. "
                                     "Ask your clinician about an A1c test. Home meter readings don't diagnose anything.",
                    escalates_if="readings rise further or you log new symptoms")
    elif len(fast) >= R["lane1_min"] and mean >= R["lane1_fasting"]:
        lane.update(lane=1, summary=f"Your fasting average of {_r(mean)} mg/dL is above {R['lane1_fasting']}.",
                    escalates_if=f"fasting readings reach {R['lane2_fasting']} mg/dL on {R['lane2_days']} days")
    else:
        lane.update(lane=1 if len(fast) >= R["lane1_min"] else None,
                    summary=f"Fasting average {_r(mean)} mg/dL." if len(fast) >= R["lane1_min"]
                    else f"{len(fast)} fasting readings so far; {R['lane1_min']} are needed before the rules apply.",
                    escalates_if=f"fasting readings reach {R['lane2_fasting']} mg/dL on {R['lane2_days']} days")
    return lane


def weights(I: Inputs) -> List[Tuple[date, float]]:
    pts = [(_dt(e["at"]).date(), e["kg"]) for e in I.entries("WEIGHT")]
    pts += [(_dt(r[0]).date(), r[2]) for r in I.rows("body_mass") if r[2] is not None]
    return sorted(pts)


def weight_lane(I: Inputs) -> Tuple[dict, bool]:
    R = L.WEIGHT_RULE
    pts = [p for p in weights(I) if p[0] >= I.today - timedelta(days=R["window_days"])]
    lane = {"area": "Weight", "lane": None, "rule": f"{R['id']} v{R['version']}", "history": "", "escalates_if": None}
    if len(pts) < 2:
        lane["summary"] = "Not enough weight entries to follow a trend."
        return lane, False
    first, last = pts[0][1], pts[-1][1]
    change = (last - first) / first
    lane["history"] = f"{len(pts)} entries since {pts[0][0].isoformat()}."
    if change <= -R["loss_fraction"]:
        lane.update(lane=2, summary=f"Your weight dropped about {abs(_r(change * 100))}% since {pts[0][0].isoformat()}. "
                                     "If you weren't trying to lose weight, mention it to your clinician.")
        return lane, True
    lane.update(lane=1, summary=f"Change since {pts[0][0].isoformat()}: {'+' if change >= 0 else ''}{_r(change * 100, 1)}%.",
                escalates_if=f"weight falls {int(R['loss_fraction'] * 100)}% or more without trying")
    return lane, False


# ---------------------------------------------------------------- change windows (§22)
def _window(readings: List[dict], a: date, b: date) -> List[dict]:
    return [r for r in readings if a <= r["t"].date() <= b]


def change_window(I: Inputs, readings: List[dict], anchor: date, label: str, set_by: str, meds: List[dict],
                  med_id: Optional[str]) -> Optional[dict]:
    R = L.CHANGE_RULE
    pre = _window(readings, anchor - timedelta(days=R["days"]), anchor - timedelta(days=1))
    post = _window(readings, anchor, anchor + timedelta(days=R["days"] - 1))
    if anchor > I.today:
        return None
    base = {"id": f"CW-{label}-{anchor.isoformat()}", "anchor": f"{label} ({anchor.isoformat()})", "measure": "Blood pressure",
            "target_set_by": set_by, "rule": f"{R['id']} v{R['version']}"}
    if len(pre) < R["min_readings"] or len(post) < R["min_readings"]:
        return dict(base, too_few=True,
                    note=f"Too few readings to compare: {len(pre)} before and {len(post)} after; "
                         f"{R['min_readings']} each are needed.")
    by_day = defaultdict(list)
    for r in pre:
        by_day[r["t"].date()].append(r["sys"])
    day_means = [st.fmean(v) for v in by_day.values()]
    spread = st.pstdev(day_means) if len(day_means) > 1 else 0.0
    ps, pd_ = st.fmean(r["sys"] for r in pre), st.fmean(r["dia"] for r in pre)
    qs, qd = st.fmean(r["sys"] for r in post), st.fmean(r["dia"] for r in post)
    diff = abs(qs - ps)
    verdict = "LARGER" if diff > 2 * spread else "SMALLER" if diff < spread else "SIMILAR"
    challenge = []
    others = [m for m in meds if m.get("med_id") != med_id and m.get("start")
              and abs((date.fromisoformat(m["start"]) - anchor).days) <= R["days"]]
    adopts = [e for e in I.entries("ADOPT") if e["active"] and abs((date.fromisoformat(e["on"]) - anchor).days) <= R["days"]]
    challenge.append({"item": "Other changes in the window",
                      "text": ("Also started within two weeks: " + ", ".join([m["name"] for m in others] +
                               [TITLES.get(e["suggestion_id"], e["suggestion_id"]) for e in adopts]) + ".") if others or adopts
                      else "No other medication starts or adopted habits were logged in this window."})
    pre_sd = st.pstdev([r["sys"] for r in pre]) or 1.0
    spike = [r for r in pre if r["t"].date() >= anchor - timedelta(days=3) and r["sys"] > ps + 2 * pre_sd]
    challenge.append({"item": "Regression to the mean",
                      "text": "Readings just before the start were unusually high; some fall back on their own." if spike
                      else "No spike right before the start."})
    src_pre, src_post = {r["source"] for r in pre}, {r["source"] for r in post}
    am = lambda rs: sum(1 for r in rs if r["t"].hour < 12) / len(rs)  # noqa: E731
    changed = src_pre != src_post or abs(am(pre) - am(post)) > 0.3
    what = []
    if src_pre != src_post:
        what.append("sources before: " + ", ".join(sorted(src_pre)) + "; after: " + ", ".join(sorted(src_post)))
    if abs(am(pre) - am(post)) > 0.3:
        what.append(f"morning readings {_r(am(pre) * 100)}% before vs {_r(am(post) * 100)}% after")
    challenge.append({"item": "Measurement change",
                      "text": ("Readings differ in how they were taken — " + "; ".join(what) + ".") if changed
                      else "Same sources and similar times of day before and after."})
    adh_text = "Adherence not tracked"
    if med_id:
        logs = [e for e in _dose_logs(I, med_id) if anchor <= _dt(e["at"]).date() <= anchor + timedelta(days=R["days"] - 1)]
        yes = sum(1 for e in logs if e["taken"] == "YES")
        adh_text = f"{yes} doses logged as taken in the {R['days']} days after the start"
        challenge.append({"item": "Adherence", "text": adh_text + "." if logs else "Doses weren't logged, so adherence is unknown."})
    else:
        challenge.append({"item": "Adherence", "text": "Days you followed the habit aren't logged, so adherence is unknown."})
    return dict(base, pre={"days": R["days"], "readings": len(pre), "mean_sys": ps, "mean_dia": pd_,
                           "daily_spread_sys": _r(spread)},
                post={"days": R["days"], "readings": len(post), "mean_sys": qs, "mean_dia": qd},
                difference_vs_spread=verdict, adherence=adh_text, challenge=challenge)


# ---------------------------------------------------------------- labs (§27)
def lab_status(I: Inputs, meds: List[dict], age: Optional[int], bp_lane_n: Optional[int]) -> dict:
    lab_dates = []
    kidney_known = kidney_concern = False
    for f in I.floats:
        if f["kind"] == "MEASUREMENT" and f.get("speaker") == "LAB_INSTRUMENT":
            d = (f.get("event_date") or {}).get("value")
            if d:
                lab_dates.append(d[:10])
            analyte = ((f.get("value") or {}).get("analyte") or "").lower()
            fresh = d and date.fromisoformat(d[:10]) >= I.today - timedelta(days=365)
            if any(k in analyte for k in ("creatinine", "egfr")) and fresh:
                kidney_known = True
                rc = (f.get("range_comparison") or {}).get("result")
                if ("creatinine" in analyte and rc == "ABOVE") or ("egfr" in analyte and rc == "BELOW"):
                    kidney_concern = True
        if f["kind"] == "ASSESSMENT" and f["polarity"] == "AFFIRMED" and "kidney" in f["concept"].lower():
            kidney_concern = True
    from .manual_labs import current_results
    typed = current_results(I.entries("LAB"))
    for row in typed:
        lab_dates.append(row["at"])
        if row["at"] >= (I.today - timedelta(days=365)).isoformat() and row["analyte"] in ("creatinine", "eGFR"):
            kidney_known = True
            # Typed flags cannot establish a clinical concern without document verification.
    last = max(lab_dates) if lab_dates else None
    current = bool(last and date.fromisoformat(last) >= I.today - timedelta(days=365))
    names = med_names(meds)
    ask = [{"test": "Metabolic panel (kidney, liver, electrolytes)", "why": "Recommended for everyone at the annual checkup; "
            "it also unlocks the potassium and protein suggestions.", "source": "PAi Health annual baseline (§27)"}]
    if age is None:
        ask.append({"test": "Cholesterol (lipids); A1c or fasting glucose",
                    "why": "Depends on your age — add your birth year in Settings to see whether these apply.",
                    "source": USPSTF_TXT})
    else:
        if 40 <= age <= 75:
            ask.append({"test": "Cholesterol (lipids)", "why": f"Recommended for adults 40–75 (you're {age}).",
                        "source": L.USPSTF_LIPID[0]})
        if 35 <= age <= 70:
            ask.append({"test": "A1c or fasting glucose", "why": "Recommended for adults 35–70 with overweight or obesity.",
                        "source": L.USPSTF_DM[0]})
    if "metformin" in names:
        ask.append({"test": "Vitamin B12", "why": "Periodic B12 checks are recommended for people on long-term metformin.",
                    "source": L.ADA_B12[0]})
    if bp_lane_n and bp_lane_n >= 1 and (names & L.BP_MEDS or bp_lane_n >= 2):
        ask.append({"test": "Urine albumin-to-creatinine ratio", "why": "These tests check how well your kidneys are "
                    "working; relevant with ongoing high blood pressure.", "source": L.ACC_AHA[0]})
    if "levothyroxine" in names:
        ask.append({"test": "Thyroid (TSH)", "why": "Usually monitored alongside levothyroxine.", "source": L.FDA_LABEL[0]})
    return {"annual_status": "CURRENT" if current else "OVERDUE", "last_entered": last or "never",
            "ask_to_include": ask, "unlocks": L.HOLD_LIST,
            "entry_methods": ["Upload a lab report (Ingest)", "Patient-portal download (Ingest)", "Enter results by hand"],
            "typed_results": typed,
            "_kidney_known": kidney_known, "_kidney_concern": kidney_concern}


USPSTF_TXT = "USPSTF screening recommendations (by age and risk)"


# ---------------------------------------------------------------- suggestions (§26)
def suggestions(I: Inputs, meds: List[dict], bp_lane_s: dict, readings: List[dict], labs: dict,
                rapid_loss: bool, cw: Callable) -> List[dict]:
    if bp_lane_s["lane"] is None or bp_lane_s["lane"] == 3 or not bp_lane_s.get("_elevated"):
        return []
    names = med_names(meds)
    chest = any(s == "chest pain" for e in I.entries("SYMPTOM") if _dt(e["at"]) >= I.now - timedelta(days=30)
                for s in e.get("urgent", []))
    flags = {"KIDNEY_CONCERN": labs["_kidney_concern"], "ACE_ARB_K": bool(names & L.ACE_ARB_K),
             "NO_KIDNEY_LABS": not labs["_kidney_known"], "CHEST_SYMPTOMS": chest, "RAPID_WEIGHT_LOSS": rapid_loss}
    moderate = any(r["sys"] >= L.BP_RULE["moderate_only"][0] or r["dia"] >= L.BP_RULE["moderate_only"][1]
                   for r in readings if r["t"].date() >= I.today - timedelta(days=14))
    on_bp_med = bool(names & L.BP_MEDS)
    adopted = {e["suggestion_id"]: e for e in I.entries("ADOPT")}
    out = []
    for s in L.BP_SUGGESTIONS:
        held = [g for g in s["gates"] if flags.get(g)]
        item = {"id": s["id"], "area": "Blood pressure", "title": s["title"], "text": s["text"],
                "state": "HELD" if held else "ACTIVE", "effect": s["effect"],
                "source": f"{L.ACC_AHA[0]} — {L.ACC_AHA[1]}", "verified": False}
        if held:
            item["held_reason"] = " ".join(L.GATE_TEXT[g] for g in held)
        notes = []
        if on_bp_med:
            notes.append("These add to your medication; they don't replace it.")
        if moderate and s["id"] in ("BP-AEROBIC", "BP-RESISTANCE"):
            notes.append("A recent reading reached 160/100 or higher: moderate effort only until it's been checked.")
        if notes:
            item["note"] = " ".join(notes)
        a = adopted.get(s["id"])
        if a and a["active"] and not held:
            item.update(adopted=True, adopted_on=a["on"])
            w = cw(date.fromisoformat(a["on"]), s["title"])
            if w and not w.get("too_few"):
                d = w["post"]["mean_sys"] - w["pre"]["mean_sys"]
                item["progress"] = (f"Average systolic {_r(w['pre']['mean_sys'])} before, {_r(w['post']['mean_sys'])} in "
                                    f"the {w['post']['days']} days after ({'+' if d >= 0 else ''}{_r(d)}); "
                                    f"{'larger than' if w['difference_vs_spread'] == 'LARGER' else 'within'} your usual "
                                    "day-to-day variation.")
            elif w:
                item["progress"] = w["note"]
        out.append(item)
    return out


# ---------------------------------------------------------------- assemble
def build(I: Inputs) -> dict:
    since90 = I.today - timedelta(days=89)
    meds = medications_in_use(I)
    readings = bp_readings(I, I.today - timedelta(days=180))
    patterns = {m["med_id"]: dose_pattern(I, m) for m in meds if m["origin"] == "LOG"}
    bpl, bp_prompts = bp_lane(I, readings)
    gl = glucose_lane(I)
    wl, rapid = weight_lane(I)
    age = (I.today.year - I.profile["birth_year"]) if I.profile.get("birth_year") else None
    labs = lab_status(I, meds, age, bpl["lane"])

    windows = []
    for m in meds:
        if m.get("start") and "bp" in (m.get("targets") or []):
            w = change_window(I, readings, date.fromisoformat(m["start"]), f"{m['name']} started", "you", meds, m["med_id"])
            if w and not w.get("too_few"):
                windows.append(w)
    cw = lambda d, label: change_window(I, readings, d, label, "you", meds, None)  # noqa: E731
    sugg = suggestions(I, meds, bpl, readings, labs, rapid, cw)

    prompts = ([] if bpl["lane"] == 3 else dose_prompts(I, meds, patterns)) + bp_prompts
    names = med_names(meds)

    # ---------------- Meals & Nutrition
    today_meals = [e for e in I.entries("MEAL") if _dt(e["at"]).date() == I.today]
    today_meals.sort(key=lambda m: m["at"])

    meals_display = []
    today_tot = defaultdict(float)
    dataset_dates = set()

    for m in today_meals:
        time_str = _dt(m["at"]).strftime("%H:%M")
        items_desc = []
        for it in m.get("items", []):
            d = it.get("description", "Food")
            lbl = it.get("portion_label")
            g = it.get("grams")
            if lbl:
                items_desc.append(f"{d} ({lbl})")
            elif g:
                items_desc.append(f"{d} ({_r(g)} g)")
            else:
                items_desc.append(d)
            for k, v in it.get("nutrients", {}).items():
                today_tot[k] += float(v or 0.0)
            prov = it.get("provenance") or {}
            if prov.get("dataset_date"):
                dataset_dates.add(prov["dataset_date"])

        lbl_text = ", ".join(items_desc) if items_desc else "Meal"
        if m.get("note"):
            lbl_text += f" — {m['note']}"
        meals_display.append({
            "time": time_str,
            "label": lbl_text,
            "method": "Logged by you (USDA FDC)",
        })

    potassium_held = bool(names & L.ACE_ARB_K) or not labs["_kidney_known"] or labs["_kidney_concern"]

    if today_meals:
        totals_display = [
            {
                "nutrient": "Calories",
                "range": f"{_r(today_tot['energy_kcal'] * 0.9):,}–{_r(today_tot['energy_kcal'] * 1.1):,}" if today_tot['energy_kcal'] > 0 else "0",
                "target": "No target set",
                "state": "INFO",
            },
            {
                "nutrient": "Sodium",
                "range": f"{_r(today_tot['sodium_mg'] * 0.9):,}–{_r(today_tot['sodium_mg'] * 1.1):,} mg" if today_tot['sodium_mg'] > 0 else "0 mg",
                "target": "Under 2,300 mg",
                "state": "WITHIN" if today_tot['sodium_mg'] <= 2300 else "ABOVE",
            },
            {
                "nutrient": "Fiber",
                "range": f"{_r(today_tot['fiber_g'] * 0.9):,}–{_r(today_tot['fiber_g'] * 1.1):,} g" if today_tot['fiber_g'] > 0 else "0 g",
                "target": "About 30 g",
                "state": "WITHIN" if today_tot['fiber_g'] >= 28 else "BELOW",
            },
            {
                "nutrient": "Potassium",
                "range": f"{_r(today_tot['potassium_mg'] * 0.9):,}–{_r(today_tot['potassium_mg'] * 1.1):,} mg" if today_tot['potassium_mg'] > 0 else "0 mg",
                "target": "Held — see suggestions" if potassium_held else "2,600–3,400 mg",
                "state": "HELD" if potassium_held else ("WITHIN" if today_tot['potassium_mg'] >= 2600 else "BELOW"),
            },
            {
                "nutrient": "Protein",
                "range": f"{_r(today_tot['protein_g'] * 0.9):,}–{_r(today_tot['protein_g'] * 1.1):,} g" if today_tot['protein_g'] > 0 else "0 g",
                "target": "No target set",
                "state": "INFO",
            },
        ]
        source_label = f"USDA FoodData Central ({', '.join(sorted(dataset_dates))})" if dataset_dates else "USDA FoodData Central"
    else:
        totals_display = []
        source_label = None

    # Daily totals across all logged meals
    meals_by_date = defaultdict(list)
    for m in I.entries("MEAL"):
        meals_by_date[_dt(m["at"]).date()].append(m)

    daily_nutrition_totals = {}
    for d, d_meals in sorted(meals_by_date.items()):
        d_tot = defaultdict(float)
        d_dates = set()
        for m in d_meals:
            for it in m.get("items", []):
                for k, v in it.get("nutrients", {}).items():
                    d_tot[k] += float(v or 0.0)
                prov = it.get("provenance") or {}
                if prov.get("dataset_date"):
                    d_dates.add(prov["dataset_date"])
        daily_nutrition_totals[d.isoformat()] = {
            "nutrients": {k: round(v, 2) for k, v in d_tot.items()},
            "source": f"USDA FoodData Central ({', '.join(sorted(d_dates))})" if d_dates else "USDA FoodData Central",
            "items_count": sum(len(m.get("items", [])) for m in d_meals),
        }

    # ---------------- Medication-Food Interactions
    interactions = []
    for drugs, food, text in L.INTERACTIONS:
        if not (names & drugs):
            continue
        pair = f"{', '.join(sorted(names & drugs))} · {food}"
        state = "CHECK"
        if "warfarin" in names and food == "Vitamin K foods":
            if today_tot["vitamin_k_ug"] > 50.0:
                state = "NOTED"
                text = (f"Logged meal items contain vitamin K ({round(today_tot['vitamin_k_ug'])} µg). "
                        "With warfarin, keeping vitamin K intake consistent from week to week rather than "
                        "changing it suddenly is worth discussing with your clinician or pharmacist.")
            else:
                state = "CHECK"
                text = ("Keep vitamin K foods consistent from week to week rather than changing them suddenly; "
                        "your clinician sets your dose around your usual intake.")
        elif (names & L.ACE_ARB_K) and "Potassium" in food:
            med_str = ', '.join(sorted(names & L.ACE_ARB_K))
            if today_tot["potassium_mg"] > 2000.0:
                state = "NOTED"
                text = (f"Logged meal items contain potassium ({round(today_tot['potassium_mg'])} mg). "
                        f"With {med_str}, high potassium intake from food or salt substitutes is "
                        "worth discussing with your clinician or pharmacist.")
            else:
                state = "GATED"
                text = (f"Potassium suggestions are held. With {med_str}, potassium supplements "
                        "and salt substitutes are worth discussing with your clinician or pharmacist.")
        interactions.append({"pair": pair, "state": state, "text": text})

    raises = []
    for e in I.entries("ITEM"):
        n = e["name"].lower()
        for label, words in L.RAISES_BP.items():
            if any(w in n for w in words) and _dt(e["at"]).date() >= I.today - timedelta(days=30):
                raises.append({"item": e["name"], "logged": _dt(e["at"]).date().isoformat(), "text": L.RAISES_BP_TEXT})
                break

    medications = []
    for m in meds:
        adh, _ = adherence(I, m)
        p = patterns.get(m["med_id"]) or {"state": "NOT_DETECTABLE", "text": "Listed in your records; add it to track doses and checks."}
        conf = None
        if m.get("sensor_class") == "DIRECT_DELIVERY":
            conf = {"sensor_class": "DIRECT_DELIVERY", "status_off": "EXPLAINED", "status_on": "VALIDATED",
                    "label": "Doses recorded by the device (your setting decides whether that confirms them)."}
        elif m.get("sensor_class") == "CONTAINER_ACCESS":
            conf = {"sensor_class": "CONTAINER_ACCESS", "status_off": "EXPLAINED", "status_on": "EXPLAINED",
                    "label": "Container opened — not the same as swallowed."}
        medications.append({"id": m["med_id"], "name": m["name"], "dose": m.get("dose") or "",
                            "start": m.get("start"), "start_basis": "logged by you" if m["origin"] == "LOG" and m.get("start")
                            else "from your records" if m["origin"] == "RECORD" else "not logged",
                            "adherence": adh, "sensor": None, "verification": {"state": p["state"], "text": p["text"]},
                            "confirmation": conf, "schedule": m.get("schedule", []), "targets": m.get("targets", []),
                            "pattern_delta": p.get("delta"),
                            "origin": m["origin"]})

    notices = []
    if labs["annual_status"] == "OVERDUE":
        notices.append({"id": "LAB-ANNUAL", "text": "This year's labs haven't been entered. Kidney function, cholesterol, "
                        "thyroid and similar can't be monitored, and suggestions are based on your device and logged data only."})

    bp_list = [{"id": f"BP-{i}", "date": r["t"].date().isoformat(), "slot": "AM" if r["t"].hour < 12 else "PM",
                "systolic": r["sys"], "diastolic": r["dia"], "device_class": r["device_class"], "standing": r["standing"]}
               for i, r in enumerate(readings) if r["t"].date() >= since90]
    rhr = daily_mean(I.rows("resting_heart_rate"), since90)
    sleep = sleep_hours(I.rows("sleep_stage"), since90)
    steps = daily_sum_primary(I.rows("steps"), since90)
    active = daily_sum_primary(I.rows("exercise_time"), since90)
    days = sorted(set(rhr) | set(sleep) | set(steps) | set(active))
    daily = [{"date": d.isoformat(), "resting_hr": _r(rhr[d], 1) if d in rhr else None,
              "sleep_hours": _r(sleep[d], 1) if d in sleep else None, "steps": _r(steps[d]) if d in steps else None,
              "active_minutes": _r(active[d]) if d in active else None, "device_class": "WATCH_OR_TRACKER"} for d in days]
    glucose = [{"date": _dt(e["at"]).date().isoformat(), "fasting_mg_dl": e["mg_dl"], "device_class": "HOME_METER"}
               for e in I.entries("GLUCOSE") if e["fasting"] and _dt(e["at"]).date() >= since90]
    markers = [{"x": m["start"], "label": f"{m['name']} started"} for m in meds if m.get("start")]

    audit = []
    for e in reversed(I.logs[-30:]):
        audit.append({"at": e.get("_created", "")[:16].replace("T", " "), "event": _describe(e), "rule": "logged by you"})

    return {
        "synthetic": False, "notice": "", "as_of": I.today.isoformat(),
        "user": {"display_name": I.user_name, "age": age, "sex": I.profile.get("sex")},
        "settings": {}, "lanes": [{k: v for k, v in x.items() if not k.startswith("_")} for x in (bpl, gl, wl)], "prompts": prompts, "notices": notices,
        "medications": medications, "change_windows": windows, "suggestions": sugg, "raises_bp": raises,
        "labs": {k: v for k, v in labs.items() if not k.startswith("_")},
        "nutrition": {
            "date": I.today.isoformat(),
            "meals": meals_display,
            "totals": totals_display,
            "source": source_label,
            "daily_totals": daily_nutrition_totals,
            "interaction_checks": interactions,
            "excluded_note": "Alkaline-diet, detox and pH-balancing programs aren't part of PAi Health because they lack an evidence base.",
        },
        "vitals": {"bp": bp_list, "daily": daily, "glucose": glucose, "lisinopril_start": markers[0]["x"] if markers else "",
                   "markers": markers},
        "audit": audit,
    }


def _describe(e: dict) -> str:
    t = e["type"]
    if t == "BP":
        return f"Blood pressure {int(e['systolic'])}/{int(e['diastolic'])}"
    if t == "DOSE":
        return f"Dose {e['taken'].lower().replace('_', ' ')}"
    if t == "MEDICATION":
        return f"Medication {'stopped' if e.get('stopped') else 'saved'}: {e['name']}"
    if t == "GLUCOSE":
        return f"Glucose {int(e['mg_dl'])} mg/dL{' fasting' if e['fasting'] else ''}"
    if t == "WEIGHT":
        return f"Weight {e['kg']} kg"
    if t == "SYMPTOM":
        return f"Symptom: {e['text']}"
    if t == "ITEM":
        return f"Took: {e['name']}"
    if t == "ADOPT":
        return f"{'Following' if e['active'] else 'Stopped following'} suggestion {e['suggestion_id']}"
    if t == "PROFILE":
        return "Profile updated"
    if t == "LAB":
        r = (e.get("results") or [{}])[0]
        return f"Lab typed in: {r.get('analyte', '')}"
    if t == "MEAL":
        count = len(e.get("items", []))
        return f"Meal logged ({count} item{'s' if count != 1 else ''})"
    return t