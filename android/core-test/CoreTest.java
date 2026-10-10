import com.pailuton.health.core.*;

import java.time.ZonedDateTime;
import java.util.*;

/** Interop check: the phone core against a real PAi Health PC (run by engine/tests/test_companion_java.py). */
public class CoreTest {
    static long seq = 0;
    static void check(boolean ok, String what) { if (!ok) { System.out.println("FAIL " + what); System.exit(1); } }

    public static void main(String[] a) throws Exception {
        Pairing.allowTestNet = true;
        Pairing p = Pairing.parse(a[0]);
        SyncClient.SeqStore store = new SyncClient.SeqStore() { public long get() { return seq; } public void set(long s) { seq = s; } };
        Map<String, Object> dev = new LinkedHashMap<>();
        dev.put("name", "Java core"); dev.put("model", "JDK"); dev.put("app_version", "test");
        SyncClient c = new SyncClient(p, store, dev);

        Map<String, Object> hello = c.hello();
        check(Boolean.TRUE.equals(hello.get("paired")), "hello");
        Rules rules = new Rules(Json.obj(hello.get("rules")));
        check(rules.safetyLine().startsWith("If you're not sure whether you took it"), "safety line");

        // a dose the phone logged, and a heart-rate sample
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("type", "BP"); entry.put("systolic", 128L); entry.put("diastolic", 80L);
        entry.put("at", ZonedDateTime.now().minusMinutes(5).format(Rules.ISO_MIN));
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("uid", UUID.randomUUID().toString()); item.put("entry", entry);
        String t = ZonedDateTime.now().minusMinutes(3).format(Rules.ISO_MIN);
        List<Object> row = Arrays.asList(t, t, 66.0, "bpm", "Pixel Watch", "health_connect", "WATCH", null, null, null, "HeartRateRecord", null);
        Map<String, Object> samples = new LinkedHashMap<>();
        samples.put("heart_rate", Collections.singletonList(row));
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("entries", Collections.singletonList(item)); payload.put("samples", samples);
        Map<String, Object> r = c.sync(payload);
        check(Long.valueOf(1).equals(r.get("accepted")), "accepted " + r);

        // replay: rewind the counter, the PC must refuse
        seq = 0;
        try { c.sync(new LinkedHashMap<>()); check(false, "replay accepted"); } catch (SyncClient.Refused e) { /* expected */ }

        // dose prompt from the bundle, same text as the PC
        Rules.DoseLog none = new Rules.DoseLog() {
            public boolean answered(String m, String d) { return false; }
            public int dosesOn(String m, java.time.LocalDate d) { return 0; }
        };
        List<Rules.DosePrompt> prompts = rules.dosePrompts(ZonedDateTime.now(), none, Collections.emptyList());
        StringBuilder out = new StringBuilder();
        for (Rules.DosePrompt dp : prompts) out.append(dp.id).append("\t").append(dp.text).append("\n");
        System.out.print(out);
        check(rules.afterBloodPressure(186, 100, Collections.singleton("chest pain")).contains("Call 911"), "lane 3");
        check(rules.afterBloodPressure(186, 100, Collections.emptySet()).contains("measure again"), "recheck");
        check(rules.afterBloodPressure(130, 80, Collections.emptySet()) == null, "normal");
        System.out.println("OK");
    }
}
