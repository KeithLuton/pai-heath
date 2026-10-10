package com.pailuton.health.core;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.LinkedHashMap;
import java.util.Map;

/** Talks to the PC. Every request carries a strictly increasing sequence number; the reply must echo it. */
public final class SyncClient {
    /** Where the last-used sequence number lives (SharedPreferences on Android, a field in tests). */
    public interface SeqStore { long get(); void set(long seq); }

    public static final class Locked extends IOException { private static final long serialVersionUID = 1L; public Locked() { super("PAi Health is locked on the computer"); } }
    public static final class Refused extends IOException { private static final long serialVersionUID = 1L; public Refused(String m) { super(m); } }

    private final Pairing pairing;
    private final byte[] kUp, kDown;
    private final SeqStore seq;
    private final Map<String, Object> device;

    public SyncClient(Pairing pairing, SeqStore seq, Map<String, Object> device) throws GeneralSecurityException {
        this.pairing = pairing;
        byte[][] k = Crypto.keys(pairing.secret, pairing.pairId);
        this.kUp = k[0]; this.kDown = k[1];
        this.seq = seq; this.device = device;
    }

    public Map<String, Object> hello() throws IOException, GeneralSecurityException {
        return call("/v1/hello", new LinkedHashMap<>());
    }

    public Map<String, Object> sync(Map<String, Object> payload) throws IOException, GeneralSecurityException {
        return call("/v1/sync", payload);
    }

    public Map<String, Object> call(String path, Map<String, Object> payload) throws IOException, GeneralSecurityException {
        long n = seq.get() + 1;
        Map<String, Object> p = new LinkedHashMap<>(payload);
        p.put("seq", n);
        p.put("device", device);
        byte[] body = Crypto.seal(kUp, pairing.pairId, "up", path, p).getBytes(StandardCharsets.UTF_8);
        HttpURLConnection c = (HttpURLConnection) new URL(pairing.baseUrl() + path).openConnection();
        c.setConnectTimeout(5000); c.setReadTimeout(30000);
        c.setRequestMethod("POST"); c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        c.setFixedLengthStreamingMode(body.length);
        try (OutputStream o = c.getOutputStream()) { o.write(body); }
        int code = c.getResponseCode();
        seq.set(n);                                  // the PC saw this number; never reuse it
        String text = read(code >= 400 ? c.getErrorStream() : c.getInputStream());
        if (code == 423) throw new Locked();
        if (code != 200) throw new Refused("The computer refused the sync (" + code + ")");
        Map<String, Object> reply = Crypto.open(kDown, pairing.pairId, "down", path, text);
        Object echoed = reply.get("seq");
        if (!(echoed instanceof Long) || (Long) echoed != n) throw new GeneralSecurityException("reply doesn't match request");
        return reply;
    }

    private static String read(InputStream in) throws IOException {
        if (in == null) return "";
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int r;
        while ((r = in.read(buf)) > 0) b.write(buf, 0, r);
        in.close();
        return b.toString("UTF-8");
    }
}
