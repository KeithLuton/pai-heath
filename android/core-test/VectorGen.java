import com.pailuton.health.core.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.time.LocalDate;
import java.time.ZonedDateTime;
import java.util.*;

/**
 * Reads rule cases (tools/gen_companion_vectors.py) and writes what the Java core decides,
 * so the iPhone core can be held to exactly the same prompts and texts.
 * Usage: java VectorGen cases.json > rules_expected.json
 */
public class VectorGen {
    public static void main(String[] a) throws Exception {
        Map<String, Object> in = Json.obj(Json.parse(new String(Files.readAllBytes(Paths.get(a[0])), StandardCharsets.UTF_8)));
        Rules rules = new Rules(Json.obj(in.get("bundle")));
        List<Object> out = new ArrayList<>();
        for (Object o : Json.arr(in.get("dose_cases"))) {
            Map<String, Object> c = Json.obj(o);
            ZonedDateTime now = ZonedDateTime.parse((String) c.get("now"));
            Set<String> answered = new HashSet<>();
            for (Object x : Json.arr(c.get("answered"))) answered.add((String) x);
            Map<String, Object> doses = Json.obj(c.get("doses"));
            List<Rules.Hr> hr = new ArrayList<>();
            for (Object x : Json.arr(c.get("hr"))) {
                List<Object> p = Json.arr(x);
                hr.add(new Rules.Hr(((Number) p.get(0)).longValue(), ((Number) p.get(1)).doubleValue()));
            }
            Rules.DoseLog log = new Rules.DoseLog() {
                public boolean answered(String m, String d) { return answered.contains(m + "|" + d); }
                public int dosesOn(String m, LocalDate d) {
                    Object n = doses.get(m + "@" + d);
                    return n == null ? 0 : ((Number) n).intValue();
                }
            };
            List<Object> prompts = new ArrayList<>();
            for (Rules.DosePrompt p : rules.dosePrompts(now, log, hr)) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("id", p.id); m.put("med_id", p.medId); m.put("due", p.due); m.put("text", p.text);
                m.put("safety_line", p.safetyLine);
                prompts.add(m);
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("name", c.get("name")); r.put("prompts", prompts);
            out.add(r);
        }
        List<Object> bp = new ArrayList<>();
        for (Object o : Json.arr(in.get("bp_cases"))) {
            Map<String, Object> c = Json.obj(o);
            Set<String> urgent = new LinkedHashSet<>();
            for (Object x : Json.arr(c.get("urgent"))) urgent.add((String) x);
            Map<String, Object> r = new LinkedHashMap<>(c);
            r.put("expected", rules.afterBloodPressure(((Number) c.get("sys")).doubleValue(),
                    ((Number) c.get("dia")).doubleValue(), urgent));
            bp.add(r);
        }
        Map<String, Object> res = new LinkedHashMap<>();
        res.put("dose", out); res.put("bp", bp);
        System.out.print(Json.write(res));
    }
}
