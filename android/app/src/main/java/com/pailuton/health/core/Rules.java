package com.pailuton.health.core;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The fixed rules the phone runs between syncs, from the bundle the PC sends.
 * Mirrors engine/tracker/compute.py dose_prompts() and bp_lane(): same texts, same timing,
 * so a prompt reads the same whether the phone or the PC raised it.
 * Prompts ask; they never tell anyone to take a dose (§21.4, §28.2).
 */
public final class Rules {
    public static final DateTimeFormatter ISO_MIN = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mmxxx");

    public static final class DosePrompt {
        public final String id, medId, due, text, safetyLine;
        public final List<String> options = java.util.Collections.unmodifiableList(java.util.Arrays.asList("Yes", "No", "Not sure"));
        DosePrompt(String id, String medId, String due, String text, String safetyLine) {
            this.id = id; this.medId = medId; this.due = due; this.text = text; this.safetyLine = safetyLine;
        }
    }

    /** A heart-rate sample: epoch seconds and bpm. */
    public static final class Hr {
        public final long t; public final double bpm;
        public Hr(long t, double bpm) { this.t = t; this.bpm = bpm; }
    }

    /** What has already been logged for a medication: due times answered, and doses per day. */
    public interface DoseLog {
        boolean answered(String medId, String due);
        int dosesOn(String medId, LocalDate day);
    }

    private final Map<String, Object> bundle;

    public Rules(Map<String, Object> bundle) { this.bundle = bundle; }

    public String safetyLine() { return (String) bundle.get("safety_line"); }

    private long rule(String k) { return ((Number) Json.obj(bundle.get("dose_rule")).get(k)).longValue(); }

    private static Double mean(Collection<Hr> hr, long a, long b) {
        double s = 0; int n = 0;
        for (Hr h : hr) if (h.t >= a && h.t < b) { s += h.bpm; n++; }
        return n >= 3 ? s / n : null;
    }

    public List<DosePrompt> dosePrompts(ZonedDateTime now, DoseLog log, Collection<Hr> hr) {
        List<DosePrompt> out = new ArrayList<>();
        long after = rule("prompt_after_min"), pre = rule("pre_min"), ps = rule("post_start_min"), pe = rule("post_end_min");
        LocalDate today = now.toLocalDate();
        for (Object o : Json.arr(bundle.get("meds"))) {
            Map<String, Object> m = Json.obj(o);
            List<Object> sched = Json.arr(m.get("schedule"));
            if (sched.isEmpty()) continue;
            LocalDate start = today.minusDays(1);
            if (m.get("start") != null) {
                LocalDate s = LocalDate.parse((String) m.get("start"));
                if (s.isAfter(start)) start = s;
            }
            String medId = (String) m.get("med_id"), name = (String) m.get("name");
            Map<String, Object> pat = Json.obj(m.get("pattern"));
            boolean qualified = "QUALIFIED".equals(pat.get("state"));
            Double delta = pat.get("delta") instanceof Number ? ((Number) pat.get("delta")).doubleValue() : null;
            for (LocalDate d = start; !d.isAfter(today); d = d.plusDays(1)) {
                for (Object so : sched) {
                    ZonedDateTime due = ZonedDateTime.of(d, LocalTime.parse((String) so), now.getZone());
                    if (due.plusMinutes(after).isAfter(now)) continue;
                    String dueIso = due.format(ISO_MIN);
                    if (log.answered(medId, dueIso) || log.dosesOn(medId, d) >= sched.size()) continue;
                    String when = (String) so, day = d.equals(today) ? "" : " yesterday";
                    boolean seen = false;
                    if (qualified && delta != null && hr != null) {
                        long t = due.toEpochSecond();
                        Double a = mean(hr, t - pre * 60, t), b = mean(hr, t + ps * 60, t + pe * 60);
                        seen = a != null && b != null && (b - a) * delta > 0 && Math.abs(b - a) >= Math.abs(delta) / 2;
                    }
                    String text = seen ? "Looks like you may have taken your " + when + " dose of " + name + day + ". Log it?"
                            : qualified ? "Your usual post-dose pattern hasn't shown up after your " + when + " dose of " + name + day + ". Did you take it?"
                            : "Did you take your " + when + " dose of " + name + day + "?";
                    out.add(new DosePrompt("DOSE|" + medId + "|" + dueIso, medId, dueIso, text, safetyLine()));
                }
            }
        }
        return out;
    }

    /** After a blood-pressure reading: the fixed emergency text, the recheck text, or null. */
    public String afterBloodPressure(double sys, double dia, Set<String> urgentSymptomsLast24h) {
        List<Object> sev = Json.arr(bundle.get("bp_severe"));
        boolean severe = sys >= ((Number) sev.get(0)).doubleValue() || dia >= ((Number) sev.get(1)).doubleValue();
        if (!severe) return null;
        if (urgentSymptomsLast24h != null && !urgentSymptomsLast24h.isEmpty())
            return ((String) bundle.get("lane3_text")).replace("{symptom}", urgentSymptomsLast24h.iterator().next());
        return (String) bundle.get("recheck_text");
    }

    public List<String> urgentSymptoms() {
        List<String> out = new ArrayList<>();
        for (Object o : Json.arr(bundle.get("urgent_symptoms"))) out.add((String) o);
        return out;
    }
}
