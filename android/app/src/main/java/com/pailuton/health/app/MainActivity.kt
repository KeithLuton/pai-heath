package com.pailuton.health.app

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.pailuton.health.core.Json
import com.pailuton.health.core.Rules
import java.time.ZonedDateTime
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * One screen: pair with the computer, then log a dose, a blood-pressure reading or a symptom.
 * Logs are "logged by you" (§18) and wait on the phone until the computer is reachable.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var store: Store
    private lateinit var body: LinearLayout
    private lateinit var hcContainer: LinearLayout
    private val hcPermissions = registerForActivityResult(PermissionController.createRequestPermissionResultContract()) { render() }
    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { render() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 64, 48, 48) }
        hcContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        setContentView(ScrollView(this).apply { addView(body) })
        if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        render()
    }

    override fun onResume() { super.onResume(); render() }

    private fun text(s: String, size: Float = 16f, bold: Boolean = false) = TextView(this).apply {
        text = s; textSize = size; if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD); setPadding(0, 12, 0, 12)
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply { text = label; setOnClickListener { onClick() } }

    private fun render() {
        body.removeAllViews()
        hcContainer.removeAllViews()

        body.addView(text("PAi Health", 26f, true))
        if (store.pairingUri == null) { renderUnpaired(); return }

        val rules = store.rulesJson?.let { Rules(Json.obj(Json.parse(it))) }
        body.addView(text("Paired with your computer. Last sync: ${store.lastSync ?: "never"}", 14f))
        store.lastError?.let { body.addView(text(it, 14f).apply { setTextColor(0xFF8A5D0B.toInt()) }) }
        val waiting = store.queue().size
        if (waiting > 0) body.addView(text("$waiting entr${if (waiting == 1) "y" else "ies"} waiting to sync", 14f))

        body.addView(button("Sync now") {
            lifecycleScope.launch {
                val m = PhoneSync.sync(this@MainActivity)
                PhoneSync.prompts(this@MainActivity)
                toast(m)
                render()
            }
        })

        body.addView(hcContainer)
        lifecycleScope.launch {
            val reader = HealthReader(this@MainActivity)
            val granted = runCatching { reader.granted() }.getOrDefault(false)
            hcContainer.removeAllViews()
            if (reader.client == null) {
                hcContainer.addView(text("Health Connect isn't available on this phone. Install it from the Play Store to share watch data.", 14f))
            } else if (!granted) {
                hcContainer.addView(button("Allow reading Health Connect") {
                    hcPermissions.launch(reader.requestablePermissions())
                })
            }
        }

        body.addView(text("Log", 20f, true))
        rules?.let { r ->
            val rulesMap = Json.obj(Json.parse(store.rulesJson!!))
            val medsRaw = rulesMap["meds"]
            val meds = if (medsRaw is List<*>) Json.arr(medsRaw) else emptyList<Any?>()
            for (o in meds) {
                val m = Json.obj(o)
                body.addView(button("I took ${m["name"]} now") {
                    store.enqueue(linkedMapOf(
                        "type" to "DOSE",
                        "med_id" to m["med_id"],
                        "due" to null,
                        "taken" to "YES",
                        "at" to ZonedDateTime.now().format(Rules.ISO_MIN)
                    ))
                    PhoneSync.syncSoon(this)
                    toast("Logged.")
                    render()
                })
            }
            body.addView(button("Blood pressure reading") { logBp(r) })
            body.addView(button("Symptom") { logSymptom(r) })
        }
        body.addView(text(getString(com.pailuton.health.R.string.privacy), 13f))
        body.addView(button("Unpair this phone") {
            AlertDialog.Builder(this)
                .setMessage("Unpair? Entries not yet synced will be lost.")
                .setPositiveButton("Unpair") { _, _ -> store.unpair(); render() }
                .setNegativeButton("Cancel", null)
                .show()
        })
    }

    private fun renderUnpaired() {
        body.addView(text("On your computer: PAi Health › Settings › Phone companion › Turn on › Pair a phone. Then scan the code it shows.", 16f))
        body.addView(button("Scan the pairing code") {
            GmsBarcodeScanning.getClient(this).startScan()
                .addOnSuccessListener { code -> code.rawValue?.let { pair(it) } }
                .addOnFailureListener { toast("Scan didn't work: ${it.message}") }
        })
        val manual = EditText(this).apply { hint = "…or paste the pairing link"; inputType = InputType.TYPE_CLASS_TEXT }
        body.addView(manual)
        body.addView(button("Pair") { pair(manual.text.toString()) })
        body.addView(text("Your phone and computer must be on the same home Wi-Fi.", 14f))
    }

    private fun pair(uri: String) {
        lifecycleScope.launch {
            val err = withContext(Dispatchers.IO) { runCatching { PhoneSync.pair(this@MainActivity, uri) }.exceptionOrNull() }
            toast(if (err == null) "Paired." else "Couldn't pair: ${err.message}")
            render()
        }
    }

    private fun logBp(rules: Rules) {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 16, 48, 0) }
        val sys = EditText(this).apply { hint = "Systolic (top)"; inputType = InputType.TYPE_CLASS_NUMBER }
        val dia = EditText(this).apply { hint = "Diastolic (bottom)"; inputType = InputType.TYPE_CLASS_NUMBER }
        box.addView(sys)
        box.addView(dia)
        box.addView(text("Seated, back supported, 5 minutes rest, arm at heart level.", 13f))
        AlertDialog.Builder(this).setTitle("Blood pressure").setView(box)
            .setPositiveButton("Log") { _, _ ->
                val s = sys.text.toString().toDoubleOrNull()
                val d = dia.text.toString().toDoubleOrNull()
                if (s == null || d == null || d >= s) { toast("Check the two numbers."); return@setPositiveButton }
                store.enqueue(linkedMapOf("type" to "BP", "systolic" to s, "diastolic" to d, "at" to ZonedDateTime.now().format(Rules.ISO_MIN)))
                PhoneSync.syncSoon(this)
                rules.afterBloodPressure(s, d, recentUrgent())?.let { msg -> urgentDialog(msg) } ?: toast("Logged.")
                render()
            }.setNegativeButton("Cancel", null).show()
    }

    private fun recentUrgent(): Set<String> {
        val cut = ZonedDateTime.now().minusHours(24)
        return store.queue().map { Json.obj(Json.obj(it)["entry"]) }
            .filter { it["type"] == "SYMPTOM" && runCatching { ZonedDateTime.parse(it["at"] as String, Rules.ISO_MIN).isAfter(cut) }.getOrDefault(false) }
            .flatMap { (it["urgent"] as? List<*>)?.map { s -> s.toString() } ?: emptyList() }.toSet()
    }

    private fun logSymptom(rules: Rules) {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 16, 48, 0) }
        val what = EditText(this).apply { hint = "What you noticed" }
        box.addView(what)
        box.addView(text("Any of these right now?", 14f, true))
        val checks = rules.urgentSymptoms().map { CheckBox(this).apply { text = it } }
        checks.forEach { box.addView(it) }
        AlertDialog.Builder(this).setTitle("Symptom").setView(box)
            .setPositiveButton("Log") { _, _ ->
                val urgent = checks.filter { it.isChecked }.map { it.text.toString() }
                if (what.text.isBlank() && urgent.isEmpty()) return@setPositiveButton
                store.enqueue(linkedMapOf(
                    "type" to "SYMPTOM",
                    "text" to what.text.toString().ifBlank { urgent.joinToString(", ") },
                    "urgent" to urgent,
                    "at" to ZonedDateTime.now().format(Rules.ISO_MIN)
                ))
                PhoneSync.syncSoon(this)
                if (urgent.isNotEmpty()) urgentDialog("If this is happening now, call 911 or your local emergency number.") else toast("Logged.")
                render()
            }.setNegativeButton("Cancel", null).show()
    }

    /** Fixed safety texts (§28.3): shown as written, with a way to dial. */
    private fun urgentDialog(message: String) {
        AlertDialog.Builder(this).setMessage(message)
            .setPositiveButton("Call 911") { _, _ -> startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:911"))) }
            .setNegativeButton("Close", null).show()
    }

    private fun toast(s: String) = android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_LONG).show()
}
