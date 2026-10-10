package com.pailuton.health.app

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BloodGlucoseRecord
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.records.metadata.Device
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import kotlin.reflect.KClass

/**
 * Reads Health Connect into the PC's row format:
 * [start, end, value, unit, source, app, device_class, method, category, conversion, raw_type, time_basis]
 * Values are passed as recorded; units are the ones the PC uses (bpm, mmHg, kg, mg/dL, %).
 */
class HealthReader(private val context: Context) {
    val client: HealthConnectClient? get() =
        if (HealthConnectClient.getSdkStatus(context) == HealthConnectClient.SDK_AVAILABLE) HealthConnectClient.getOrCreate(context) else null

    companion object {
        val TYPES: List<KClass<out Record>> = listOf(
            HeartRateRecord::class, RestingHeartRateRecord::class, StepsRecord::class,
            SleepSessionRecord::class, BloodPressureRecord::class, WeightRecord::class,
            BloodGlucoseRecord::class, OxygenSaturationRecord::class
        )
        val REQUIRED_PERMISSIONS: Set<String> = TYPES.map { HealthPermission.getReadPermission(it) }.toSet()
        val PERMISSIONS: Set<String> = REQUIRED_PERMISSIONS +
            HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND
        private val ISO = DateTimeFormatter.ISO_OFFSET_DATE_TIME
    }

    /**
     * Inspect feature status so devices without background-read capability
     * (e.g. Android 13 APK) don't fail when requesting permissions.
     */
    fun requestablePermissions(): Set<String> {
        val c = client ?: return REQUIRED_PERMISSIONS
        val hasBg = try {
            c.features.getFeatureStatus(
                HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND
            ) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE
        } catch (_: Exception) {
            false
        }
        return if (hasBg) PERMISSIONS else REQUIRED_PERMISSIONS
    }

    private fun iso(t: Instant, off: ZoneOffset?): String =
        OffsetDateTime.ofInstant(t, off ?: ZoneId.systemDefault().rules.getOffset(t)).withNano(0).format(ISO)

    private fun deviceClass(m: Metadata): String = when (m.device?.type) {
        Device.TYPE_WATCH -> "WATCH"
        Device.TYPE_PHONE -> "PHONE"
        Device.TYPE_SCALE -> "SMART_SCALE"
        Device.TYPE_RING -> "WATCH_OR_TRACKER"
        Device.TYPE_FITNESS_BAND -> "WATCH_OR_TRACKER"
        Device.TYPE_CHEST_STRAP -> "CHEST_STRAP"
        else -> "OTHER_SOURCE"
    }

    private fun row(a: Instant, ao: ZoneOffset?, b: Instant, bo: ZoneOffset?, v: Double?, unit: String, m: Metadata,
                    raw: String, category: String? = null): List<Any?> =
        listOf(iso(a, ao), iso(b, bo), v, unit, m.dataOrigin.packageName, "health_connect", deviceClass(m), null, category,
            null, raw, "DEVICE_CLOCK")

    private suspend fun <T : Record> all(type: KClass<T>, from: Instant, to: Instant): List<T> {
        val c = client ?: return emptyList()
        val out = mutableListOf<T>()
        var token: String? = null
        val effectiveFrom = if (from.isAfter(to)) to.minus(1, ChronoUnit.MINUTES) else from
        do {
            val r = c.readRecords(ReadRecordsRequest(type, TimeRangeFilter.between(effectiveFrom, to), pageToken = token))
            out += r.records
            token = r.pageToken
        } while (!token.isNullOrEmpty()) // Guard against Health Connect providers that return "" instead of null
        return out
    }

    suspend fun granted(): Boolean = client?.permissionController?.getGrantedPermissions()?.containsAll(REQUIRED_PERMISSIONS) == true

    /** Samples by measure, from `from` to now. */
    suspend fun read(from: Instant, to: Instant): Map<String, List<List<Any?>>> {
        val out = mutableMapOf<String, MutableList<List<Any?>>>()
        fun add(measure: String, r: List<Any?>) { out.getOrPut(measure) { mutableListOf() }.add(r) }

        for (r in all(HeartRateRecord::class, from, to)) for (s in r.samples)
            add("heart_rate", row(s.time, r.startZoneOffset, s.time, r.startZoneOffset, s.beatsPerMinute.toDouble(), "bpm", r.metadata, "HeartRateRecord"))
        for (r in all(RestingHeartRateRecord::class, from, to))
            add("resting_heart_rate", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.beatsPerMinute.toDouble(), "bpm", r.metadata, "RestingHeartRateRecord"))
        for (r in all(StepsRecord::class, from, to))
            add("steps", row(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset, r.count.toDouble(), "count", r.metadata, "StepsRecord"))
        for (r in all(SleepSessionRecord::class, from, to)) {
            if (r.stages.isEmpty()) add("sleep_stage", row(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset, null, "interval", r.metadata, "SleepSessionRecord", "ASLEEP_UNSPECIFIED"))
            for (st in r.stages) add("sleep_stage", row(st.startTime, r.startZoneOffset, st.endTime, r.endZoneOffset, null, "interval", r.metadata,
                "SleepSessionRecord", when (st.stage) {
                    SleepSessionRecord.STAGE_TYPE_DEEP -> "DEEP"
                    SleepSessionRecord.STAGE_TYPE_LIGHT -> "LIGHT"
                    SleepSessionRecord.STAGE_TYPE_REM -> "REM"
                    SleepSessionRecord.STAGE_TYPE_AWAKE -> "AWAKE"
                    SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED -> "AWAKE"
                    SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "AWAKE"
                    SleepSessionRecord.STAGE_TYPE_SLEEPING -> "ASLEEP_UNSPECIFIED"
                    else -> "UNKNOWN"
                }))
        }
        for (r in all(BloodPressureRecord::class, from, to)) {
            add("bp_systolic", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.systolic.inMillimetersOfMercury, "mmHg", r.metadata, "BloodPressureRecord"))
            add("bp_diastolic", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.diastolic.inMillimetersOfMercury, "mmHg", r.metadata, "BloodPressureRecord"))
        }
        for (r in all(WeightRecord::class, from, to))
            add("body_mass", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.weight.inKilograms, "kg", r.metadata, "WeightRecord"))
        for (r in all(BloodGlucoseRecord::class, from, to)) {
            add("glucose", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.level.inMilligramsPerDeciliter, "mg/dL", r.metadata, "BloodGlucoseRecord"))
        }
        for (r in all(OxygenSaturationRecord::class, from, to))
            add("spo2", row(r.time, r.zoneOffset, r.time, r.zoneOffset, r.percentage.value, "%", r.metadata, "OxygenSaturationRecord"))
        return out
    }

    /** Heart rate around now, for the dose-pattern check on the phone. */
    suspend fun heartRate(from: Instant, to: Instant): List<com.pailuton.health.core.Rules.Hr> =
        all(HeartRateRecord::class, from, to).flatMap { r -> r.samples.map { com.pailuton.health.core.Rules.Hr(it.time.epochSecond, it.beatsPerMinute.toDouble()) } }
}
