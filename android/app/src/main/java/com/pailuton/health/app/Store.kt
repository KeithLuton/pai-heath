package com.pailuton.health.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.pailuton.health.core.Json
import com.pailuton.health.core.SyncClient
import java.security.KeyStore
import java.time.LocalDate
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Everything the phone keeps, encrypted with a key that never leaves the Android Keystore:
 * the pairing (it holds the shared secret), the sequence counter, the rules bundle from the PC,
 * entries waiting to sync, and which dose prompts were answered.
 */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("paihealth.store", Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).build())
        return gen.generateKey()
    }

    private fun put(name: String, value: String?) {
        if (value == null) { prefs.edit().remove(name).apply(); return }
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val ct = c.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(name, Base64.encodeToString(c.iv + ct, Base64.NO_WRAP)).apply()
    }

    private fun get(name: String): String? {
        val raw = prefs.getString(name, null) ?: return null
        val b = Base64.decode(raw, Base64.NO_WRAP)
        if (b.size < 12) return null
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, b, 0, 12))
        return String(c.doFinal(b, 12, b.size - 12), Charsets.UTF_8)
    }

    var pairingUri: String?
        get() = get("pairing")
        set(v) = put("pairing", v)

    var rulesJson: String?
        get() = get("rules")
        set(v) = put("rules", v)

    var lastSync: String?
        get() = prefs.getString("last_sync", null)
        set(v) { prefs.edit().putString("last_sync", v).apply() }

    var lastError: String?
        get() = prefs.getString("last_error", null)
        set(v) { prefs.edit().putString("last_error", v).apply() }

    /** Health Connect: read from here forward next time (epoch millis). */
    var hcCursor: Long
        get() = prefs.getLong("hc_cursor", 0L)
        set(v) { prefs.edit().putLong("hc_cursor", v).apply() }

    val seqStore = object : SyncClient.SeqStore {
        override fun get(): Long = prefs.getLong("seq", 0L)
        override fun set(seq: Long) { prefs.edit().putLong("seq", seq).commit() }
    }

    // ---- entries waiting to sync: [{"uid":..., "entry": {...}}]
    @Synchronized fun queue(): MutableList<Any?> = get("queue")?.let { Json.arr(Json.parse(it)) } ?: mutableListOf()

    @Synchronized fun enqueue(entry: Map<String, Any?>) {
        val q = queue()
        q.add(linkedMapOf("uid" to UUID.randomUUID().toString(), "entry" to entry))
        put("queue", Json.write(q))
        if (entry["type"] == "DOSE") markDose(entry["med_id"] as String, entry["due"] as String?, entry["at"] as String)
    }

    @Synchronized fun dropSynced(uids: Set<String>) {
        put("queue", Json.write(queue().filter { Json.obj(it)["uid"] !in uids }))
    }

    // ---- what's been answered, so a prompt isn't raised twice
    private fun answered(): MutableMap<String, Any?> = get("answered")?.let { Json.obj(Json.parse(it)) } ?: linkedMapOf()

    @Synchronized private fun markDose(medId: String, due: String?, at: String) {
        val a = answered()
        if (due != null) a["$medId|$due"] = true
        val day = "$medId@" + at.substring(0, 10)
        a[day] = ((a[day] as? Number)?.toLong() ?: 0L) + 1L
        put("answered", Json.write(a))
    }

    fun doseLog() = object : com.pailuton.health.core.Rules.DoseLog {
        override fun answered(medId: String, due: String): Boolean {
            val a = this@Store.answered()
            return a["$medId|$due"] == true
        }
        override fun dosesOn(medId: String, day: LocalDate): Int {
            val a = this@Store.answered()
            return ((a["$medId@$day"] as? Number)?.toLong() ?: 0L).toInt()
        }
    }

    var posted: Set<String>
        get() = prefs.getStringSet("posted", emptySet()) ?: emptySet()
        set(v) { prefs.edit().putStringSet("posted", v.toList().takeLast(200).toSet()).apply() }

    fun unpair() { prefs.edit().clear().commit() }

    companion object { private const val ALIAS = "paihealth.store.v1" }
}
