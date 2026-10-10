package com.pailuton.health.core;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** Companion protocol v1 envelope — mirrors engine/companion/protocol.py exactly. */
public final class Crypto {
    public static final int VERSION = 1;
    private static final SecureRandom RNG = new SecureRandom();

    private Crypto() {}

    public static String b64e(byte[] b) { return Base64.getUrlEncoder().withoutPadding().encodeToString(b); }

    public static byte[] b64d(String s) { return Base64.getUrlDecoder().decode(s); }

    /** RFC 5869 HKDF-SHA256, 32-byte output. */
    public static byte[] hkdf(byte[] ikm, byte[] salt, String info) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(salt.length == 0 ? new byte[32] : salt, "HmacSHA256"));
        byte[] prk = mac.doFinal(ikm);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        mac.update(info.getBytes(StandardCharsets.UTF_8));
        mac.update((byte) 1);
        return mac.doFinal();                       // first block = 32 bytes
    }

    public static byte[][] keys(byte[] secret, String pairId) throws GeneralSecurityException {
        byte[] salt = pairId.getBytes(StandardCharsets.UTF_8);
        return new byte[][]{hkdf(secret, salt, "paihealth/companion/v1/up"), hkdf(secret, salt, "paihealth/companion/v1/down")};
    }

    static byte[] aad(String pairId, String direction, String path) {
        return ("paihealth/v" + VERSION + "|" + pairId + "|" + direction + "|" + path).getBytes(StandardCharsets.UTF_8);
    }

    public static String seal(byte[] key, String pairId, String direction, String path, Map<String, Object> payload)
            throws GeneralSecurityException {
        byte[] nonce = new byte[12];
        RNG.nextBytes(nonce);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        c.updateAAD(aad(pairId, direction, path));
        byte[] ct = c.doFinal(Json.write(payload).getBytes(StandardCharsets.UTF_8));
        Map<String, Object> env = new LinkedHashMap<>();
        env.put("v", (long) VERSION); env.put("id", pairId); env.put("n", b64e(nonce)); env.put("c", b64e(ct));
        return Json.write(env);
    }

    public static Map<String, Object> open(byte[] key, String pairId, String direction, String path, String body)
            throws GeneralSecurityException {
        Map<String, Object> env = Json.obj(Json.parse(body));
        if (!Long.valueOf(VERSION).equals(env.get("v")) || !pairId.equals(env.get("id")))
            throw new GeneralSecurityException("wrong envelope");
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, b64d((String) env.get("n"))));
        c.updateAAD(aad(pairId, direction, path));
        return Json.obj(Json.parse(new String(c.doFinal(b64d((String) env.get("c"))), StandardCharsets.UTF_8)));
    }
}
