package com.pailuton.health.core;

import java.net.URLDecoder;
import java.util.HashMap;
import java.util.Map;

/** What the QR code on the PC carries: paihealth://pair?v=1&h=<ip>&p=<port>&id=<pair id>&k=<secret> */
public final class Pairing {
    public final String host, pairId;
    public final int port;
    public final byte[] secret;

    public Pairing(String host, int port, String pairId, byte[] secret) {
        this.host = host; this.port = port; this.pairId = pairId; this.secret = secret;
    }

    /** Tests only: the PC-side interop test runs on a documentation-range address. */
    public static boolean allowTestNet = false;

    public static Pairing parse(String uri) {
        String t = uri.trim();
        if (!t.startsWith("paihealth://pair?")) throw new IllegalArgumentException("Not a PAi Health pairing code");
        Map<String, String> q = new HashMap<>();
        for (String kv : t.substring(t.indexOf('?') + 1).split("&")) {
            int e = kv.indexOf('=');
            if (e > 0) {
                try { q.put(kv.substring(0, e), URLDecoder.decode(kv.substring(e + 1), "UTF-8")); }
                catch (java.io.UnsupportedEncodingException x) { throw new IllegalStateException(x); }
            }
        }
        if (!"1".equals(q.get("v"))) throw new IllegalArgumentException("Unsupported pairing version");
        String h = q.get("h");
        boolean lan = h != null && (h.startsWith("10.") || h.startsWith("192.168.") || h.matches("172\\.(1[6-9]|2\\d|3[01])\\..*")
                || (allowTestNet && h.startsWith("192.0.2.")));
        if (!lan) throw new IllegalArgumentException("The computer must be on a private home network");
        byte[] k = Crypto.b64d(q.get("k"));
        if (k.length != 32) throw new IllegalArgumentException("Bad pairing key");
        return new Pairing(h, Integer.parseInt(q.get("p")), q.get("id"), k);
    }

    public String baseUrl() { return "http://" + host + ":" + port; }
}
