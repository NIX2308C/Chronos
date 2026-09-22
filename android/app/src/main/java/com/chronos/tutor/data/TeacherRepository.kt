package com.chronos.tutor.data

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ChatDone
import com.chronos.tutor.net.stringOrNull
import com.chronos.tutor.net.toChatDone
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** COURSE_SETTINGS_DEFAULTS in app.py, minus the retired additional_instructions. */
data class CourseSettings(
    val groundedOnly: Boolean = true,
    val guideNotComplete: Boolean = true,
    val stateUncertainty: Boolean = true,
    val practiceTools: Boolean = false,
    val visualTools: Boolean = false,
    val studyMaterials: Boolean = false,
    val revealFinalAnswers: Boolean = false,
    val workedExamples: Boolean = false,
    val hintStrength: String = "progressive",   // light | progressive | strong
)

data class CustomRule(val id: String, val text: String)

/** One uploaded file: its chunks grouped by the shared `file_<ms>` id prefix. */
data class CourseDoc(
    val name: String,
    val base: String,
    val chunks: Int,
    val addedMs: Long?,
    val summary: String?,
    val topics: List<String>,
)

data class Material(
    val rules: List<CustomRule>,
    val docs: List<CourseDoc>,
    val docsTruncated: Boolean,
)

data class QuestionRow(val question: String, val count: Int, val students: Int)

data class Stats(
    val totalQuestions: Int,
    val studentsActive: Int,
    val studentsTotal: Int,
    val grounded: Int,
    val unansweredCount: Int,
    val categories: List<Pair<String, Int>>,
    val repeats: List<QuestionRow>,
    val unanswered: List<QuestionRow>,
    val learningGaps: List<QuestionRow>,
    val behaviorConcerns: List<QuestionRow>,
)

data class Member(val uid: String, val email: String)

data class StudentProfile(
    val messages: Int,
    val summary: String,
    val stickingPoints: List<Pair<String, Int>>,
    val minMessages: Int,
)

class TeacherRepository(private val api: Api) {

    suspend fun createCourse(name: String): CourseClass = io {
        val o = api.post("/classes", obj { put("name", name.trim()) })
        CourseClass(
            id = o["id"]?.stringOrNull().orEmpty(),
            name = o["name"]?.stringOrNull().orEmpty(),
            toolkits = o.strings("toolkits"),
            joinCode = o["join_code"]?.stringOrNull(),
        )
    }

    suspend fun deleteCourse(classId: String) = io { api.delete("/classes/$classId"); Unit }

    suspend fun settings(classId: String): CourseSettings =
        io { parseSettings(api.get("/course-settings?class_id=$classId")) }

    suspend fun saveSettings(classId: String, s: CourseSettings): CourseSettings = io {
        parseSettings(api.post("/course-settings", obj {
            put("class_id", classId)
            put("settings", settingsJson(s))
        }))
    }

    suspend fun material(classId: String): Material =
        io { parseMaterial(api.post("/rules", obj { put("class_id", classId) })) }

    /** Add (no id) or edit (existing id) rules in one call. */
    suspend fun saveRules(classId: String, rules: List<CustomRule>) = io {
        api.post("/ingest", obj {
            put("class_id", classId)
            put("items", buildJsonArray {
                rules.forEach { r ->
                    add(buildJsonObject {
                        if (r.id.isNotEmpty()) put("id", r.id)
                        put("text", r.text)
                    })
                }
            })
        }); Unit
    }

    suspend fun deleteRule(classId: String, id: String) = io {
        api.post("/delete_rule", obj { put("class_id", classId); put("id", id) }); Unit
    }

    /** Server finds every chunk of the upload, so no client-side batching. */
    suspend fun deleteDoc(classId: String, base: String) = io {
        api.post("/delete_rule", obj { put("class_id", classId); put("document", base) }); Unit
    }

    /** Synchronous: returns once the file is chunked and indexed. Returns the server's warning, if any. */
    suspend fun upload(classId: String, name: String, mime: String?, bytes: ByteArray): String? = io {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("class_id", classId)
            .addFormDataPart("file", name, bytes.toRequestBody(mime?.toMediaTypeOrNull()))
            .build()
        api.execute(Request.Builder().url(api.url("/upload")).post(body).build(), slow = true)["warning"]
            ?.stringOrNull()
    }

    /** "Check what the course can answer": a non-streamed, unsaved /chat. */
    suspend fun probe(classId: String, message: String): ChatDone = io {
        api.post("/chat", obj { put("class_id", classId); put("message", message) }, slow = true).toChatDone()
    }

    suspend fun stats(classId: String, days: Int): Stats = io {
        parseStats(api.post("/stats", obj {
            put("class_id", classId); put("limit", 200); put("days", days)
        }, slow = true))
    }

    suspend fun roster(classId: String): List<Member> = io {
        (api.post("/roster", obj { put("class_id", classId) })["members"] as? JsonArray).objects().mapNotNull {
            Member(it["uid"]?.stringOrNull() ?: return@mapNotNull null, it["email"]?.stringOrNull().orEmpty())
        }
    }

    suspend fun profile(classId: String, uid: String): StudentProfile = io {
        val o = api.post("/student-profile", obj { put("class_id", classId); put("student_uid", uid) })
        StudentProfile(
            messages = o.int("messages"),
            summary = o["summary"]?.stringOrNull().orEmpty(),
            stickingPoints = (o["sticking_points"] as? JsonArray).objects().mapNotNull {
                (it["topic"]?.stringOrNull() ?: return@mapNotNull null) to it.int("times")
            },
            minMessages = o.int("min_messages"),
        )
    }

    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }
}

// ---- parsing, kept pure so it is unit-testable ------------------------------

internal fun parseSettings(o: JsonObject): CourseSettings {
    val s = o["settings"] as? JsonObject ?: return CourseSettings()
    val d = CourseSettings()
    fun b(k: String, def: Boolean) = s[k]?.let { runCatching { it.jsonPrimitive.boolean }.getOrNull() } ?: def
    return CourseSettings(
        groundedOnly = b("grounded_only", d.groundedOnly),
        guideNotComplete = b("guide_not_complete", d.guideNotComplete),
        stateUncertainty = b("state_uncertainty", d.stateUncertainty),
        practiceTools = b("practice_tools", d.practiceTools),
        visualTools = b("visual_tools", d.visualTools),
        studyMaterials = b("study_materials", d.studyMaterials),
        revealFinalAnswers = b("reveal_final_answers", d.revealFinalAnswers),
        workedExamples = b("worked_examples", d.workedExamples),
        hintStrength = s["hint_strength"]?.stringOrNull() ?: d.hintStrength,
    )
}

internal fun settingsJson(s: CourseSettings): JsonObject = buildJsonObject {
    put("grounded_only", s.groundedOnly)
    put("guide_not_complete", s.guideNotComplete)
    put("state_uncertainty", s.stateUncertainty)
    put("practice_tools", s.practiceTools)
    put("visual_tools", s.visualTools)
    put("study_materials", s.studyMaterials)
    put("reveal_final_answers", s.revealFinalAnswers)
    put("worked_examples", s.workedExamples)
    put("hint_strength", s.hintStrength)
}

/** Mirrors computeDocs() in web/teacherknowledge.html: rows with a source are file chunks. */
internal fun parseMaterial(o: JsonObject): Material {
    val rows = (o["rules"] as? JsonArray).objects()
    val manifest = o["manifest"] as? JsonObject
    val rules = rows.filter { it["source"]?.stringOrNull() == null }.mapNotNull {
        CustomRule(it["id"]?.stringOrNull() ?: return@mapNotNull null, it["text"]?.stringOrNull().orEmpty())
    }
    val docs = rows.mapNotNull { r -> r["source"]?.stringOrNull()?.let { it to r["id"]?.stringOrNull().orEmpty() } }
        .groupBy({ it.first }, { it.second })
        .map { (name, ids) ->
            val parts = ids.first().split("_")
            val base = if (parts.size > 1) parts[0] + "_" + parts[1] else ""
            val m = manifest?.get(base) as? JsonObject
            CourseDoc(
                name = name,
                base = base,
                chunks = ids.size,
                addedMs = ids.mapNotNull { it.split("_").getOrNull(1)?.toLongOrNull() }.minOrNull(),
                summary = m?.get("summary")?.stringOrNull()?.takeIf { it.isNotBlank() },
                topics = m?.strings("topics") ?: emptyList(),
            )
        }
        .sortedByDescending { it.addedMs ?: 0 }
    return Material(rules, docs, o["docs_truncated"]?.let {
        runCatching { it.jsonPrimitive.boolean }.getOrNull()
    } ?: false)
}

internal fun parseStats(o: JsonObject): Stats {
    fun rows(k: String) = (o[k] as? JsonArray).objects().mapNotNull {
        QuestionRow(it["question"]?.stringOrNull() ?: return@mapNotNull null, it.int("count"), it.int("students"))
    }
    return Stats(
        totalQuestions = o.int("total_questions"),
        studentsActive = o.int("students_active"),
        studentsTotal = o.int("students_total"),
        grounded = o.int("grounded_conversations"),
        unansweredCount = o.int("unanswered_count"),
        categories = (o["categories"] as? JsonArray).objects().mapNotNull {
            (it["name"]?.stringOrNull() ?: return@mapNotNull null) to it.int("count")
        },
        repeats = rows("repeats"),
        unanswered = rows("unanswered"),
        learningGaps = rows("learning_gaps"),
        behaviorConcerns = rows("behavior_concerns"),
    )
}

private fun JsonArray?.objects(): List<JsonObject> = this?.mapNotNull { it as? JsonObject } ?: emptyList()
private fun JsonObject.strings(k: String): List<String> =
    (this[k] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: emptyList()
private fun JsonObject.int(k: String): Int = this[k]?.let { runCatching { it.jsonPrimitive.int }.getOrNull() } ?: 0
private inline fun obj(block: kotlinx.serialization.json.JsonObjectBuilder.() -> Unit): JsonElement =
    buildJsonObject(block)
