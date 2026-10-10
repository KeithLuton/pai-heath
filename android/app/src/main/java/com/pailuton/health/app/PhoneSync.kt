package com.pailuton.health.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.pailuton.health.core.Json
import com.pailuton.health.core.Pairing
import com.pailuton.health.core.Rules
import com.pailuton.health.core.SyncClient
import java.time.Instant
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** One place for "talk to the computer" and "raise prompts", used by the screen and by the background workers. */
object PhoneSync {
    const val CHANNEL = "doses"

    fun device(): Map<String, Any?> = linkedMapOf(
        "name" to (Build.MODEL ?: "Android"),
        "model" to "${Build.MANUFACTURER} ${Build.MODEL}",
        "app_version" to "0.1.0"
    )

    fun client(store: Store): SyncClient? =
        store.pairingUri?.let { SyncClient(Pairing.parse(it), store.seqStore, device()) }

    /** Pair: parse the QR text, say hello, keep the pairing and the rules. Throws with a readable message on failure. */
    fun pair(context: Context, uri: String) {
        val store = Store(context)
        val p = Pairing.parse(uri)
        store.unpair()
        val reply = SyncClient(p, store.seqStore, device()).hello()
        store.pairingUri = uri
        store.rulesJson = Json.write(reply["rules"])
        store.lastSync = reply["server_time"] as String?
        schedule(context)
    }

    /** Send queued entries and new Health Connect data; take back the current rules. */
    suspend fun sync(context: Context): String = withContext(Dispatchers.IO) { syncBlocking(context) }

    private suspend fun syncBlocking(context: Context): String {
        val store = Store(context)
        val c = client(store) ?: return "Not paired"
        val queue = store.queue()
        val reader = HealthReader(context)
        val to = Instant.now()
        val from = if (store.hcCursor > 0) Instant.ofEpochMilli(store.hcCursor).minus(10, ChronoUnit.MINUTES)
                   else to.minus(7, ChronoUnit.DAYS)
        val samples = if (reader.granted()) reader.read(from, to) else emptyMap()
        return try {
            val reply = c.sync(linkedMapOf("entries" to queue, "samples" to samples))
            store.dropSynced(queue.map { Json.obj(it)["uid"] as String }.toSet())
            if (samples.isNotEmpty() || reader.granted()) store.hcCursor = to.toEpochMilli()
            store.rulesJson = Json.write(reply["rules"])
            store.lastSync = reply["server_time"] as String?
            store.lastError = null
            "Synced: ${reply["accepted"]} new entries"
        } catch (e: SyncClient.Locked) {
            store.lastError = "PAi Health is locked on the computer — open it to sync."
            store.lastError!!
        } catch (e: Exception) {
            store.lastError = "Couldn't reach the computer (${e.message}). Entries stay on the phone until it can."
            store.lastError!!
        }
    }

    /** Raise any dose prompt the rules call for that hasn't been raised or answered yet. */
    suspend fun prompts(context: Context) {
        val store = Store(context)
        val rules = store.rulesJson?.let { Rules(Json.obj(Json.parse(it))) } ?: return
        if (!notificationsAllowed(context)) return
        val now = ZonedDateTime.now()
        val hr = try {
            HealthReader(context).heartRate(now.minusDays(1).toInstant(), now.toInstant())
        } catch (_: Exception) {
            emptyList()
        }
        val posted = store.posted.toMutableSet()
        for (p in rules.dosePrompts(now, store.doseLog(), hr)) {
            if (p.id in posted) continue
            notifyDose(context, p)
            posted += p.id
        }
        store.posted = posted
    }

    fun notificationsAllowed(context: Context) = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun notifyDose(context: Context, p: Rules.DosePrompt) {
        if (!notificationsAllowed(context)) return
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Dose check-ins", NotificationManager.IMPORTANCE_DEFAULT))
        val nid = p.id.hashCode()
        fun action(answer: String, code: Int) = PendingIntent.getBroadcast(context, nid * 4 + code,
            Intent(context, DoseActionReceiver::class.java).putExtra("med_id", p.medId).putExtra("due", p.due)
                .putExtra("answer", answer).putExtra("nid", nid),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle("Dose check-in")
            .setContentText(p.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(p.text + "\n\n" + p.safetyLine))   // §21.4, always shown
            .addAction(0, "Yes", action("YES", 1))
            .addAction(0, "No", action("NO", 2))
            .addAction(0, "Not sure", action("NOT_SURE", 3))
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(nid, n)
    }

    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context)
        val net = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()   // home Wi-Fi
        wm.enqueueUniquePeriodicWork("sync", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES).setConstraints(net).build())
        wm.enqueueUniquePeriodicWork("prompts", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<PromptWorker>(15, TimeUnit.MINUTES).build())
    }

    fun syncSoon(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<SyncWorker>().build())
    }
}

class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        PhoneSync.sync(applicationContext)
        PhoneSync.prompts(applicationContext)
        return Result.success()
    }
}

class PromptWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        PhoneSync.prompts(applicationContext)
        return Result.success()
    }
}

/** Yes / No / Not sure on a dose notification: logged by you, queued for the computer. Nothing is auto-logged. */
class DoseActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val answer = intent.getStringExtra("answer") ?: return
        val due = intent.getStringExtra("due")
        Store(context).enqueue(linkedMapOf(
            "type" to "DOSE",
            "med_id" to intent.getStringExtra("med_id"),
            "due" to due,
            "taken" to answer,
            "at" to if (answer == "YES" && due != null) due else ZonedDateTime.now().format(Rules.ISO_MIN)
        ))
        NotificationManagerCompat.from(context).cancel(intent.getIntExtra("nid", 0))
        PhoneSync.syncSoon(context)
    }
}
