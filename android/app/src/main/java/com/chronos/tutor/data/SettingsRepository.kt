package com.chronos.tutor.data

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.stringOrNull
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** PREFERENCE_DEFAULTS in app.py. Style only: they never override course rules. */
data class TutorPrefs(
    val personality: String = "default",
    val length: String = "balanced",          // short | balanced | detailed
    val readingLevel: String = "auto",        // auto | simple | standard | advanced
    val explainSimply: Boolean = false,
    val language: String = "",
    val styleNote: String = "",
)

data class Personality(val id: String, val label: String)

/** /status/public: one of operational | degraded | checking per service. */
data class PublicStatus(val overall: String, val services: List<Pair<String, String>>, val checkedAt: String?)

class SettingsRepository(private val api: Api) {

    suspend fun preferences(): Pair<TutorPrefs, List<Personality>> =
        withContext(Dispatchers.IO) { parsePrefs(api.get("/me/preferences")) }

    /** Partial updates merge server-side; the response is the whole normalised set. */
    suspend fun savePreferences(p: TutorPrefs): Pair<TutorPrefs, List<Personality>> = withContext(Dispatchers.IO) {
        parsePrefs(api.post("/me/preferences", buildJsonObject {
            put("preferences", buildJsonObject {
                put("personality", p.personality)
                put("length", p.length)
                put("reading_level", p.readingLevel)
                put("explain_simply", p.explainSimply)
                put("language", p.language)
                put("style_note", p.styleNote)
            })
        }))
    }

    suspend fun publicStatus(): PublicStatus = withContext(Dispatchers.IO) {
        val o = api.get("/status/public")
        PublicStatus(
            overall = o["status"]?.stringOrNull() ?: "checking",
            services = (o["services"] as? JsonObject)?.map { (k, v) -> k to (v.stringOrNull() ?: "checking") } ?: emptyList(),
            checkedAt = o["checked_at"]?.stringOrNull(),
        )
    }

    /** Developer only (403 otherwise). Flattened to label/value rows for display. */
    suspend fun deployment(): List<Pair<String, String>> = withContext(Dispatchers.IO) {
        val o = api.get("/status/deployment")
        buildList {
            add("status" to (o["status"]?.stringOrNull() ?: "unknown"))
            for (section in listOf("build", "service")) {
                (o[section] as? JsonObject)?.forEach { (k, v) ->
                    v.stringOrNull()?.takeIf { k != "console_url" }?.let { add("$section $k" to it) }
                }
            }
        }
    }
}

internal fun parsePrefs(o: JsonObject): Pair<TutorPrefs, List<Personality>> {
    val p = o["preferences"] as? JsonObject
    val d = TutorPrefs()
    fun s(k: String, def: String) = p?.get(k)?.stringOrNull() ?: def
    return TutorPrefs(
        personality = s("personality", d.personality),
        length = s("length", d.length),
        readingLevel = s("reading_level", d.readingLevel),
        explainSimply = s("explain_simply", "false") == "true",
        language = s("language", ""),
        styleNote = s("style_note", ""),
    ) to ((o["personalities"] as? JsonArray)?.mapNotNull { e ->
        val x = e as? JsonObject ?: return@mapNotNull null
        Personality(x["id"]?.stringOrNull() ?: return@mapNotNull null, x["label"]?.stringOrNull().orEmpty())
    } ?: emptyList())
}
