package com.chronos.tutor.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import com.chronos.tutor.net.Api
import com.chronos.tutor.net.stringOrNull

private val Context.dataStore by preferencesDataStore(name = "chronos")

/**
 * The browser state that had to be re-homed, and nothing more.
 *
 * There is no Room database on purpose: firestore.rules denies all client
 * access and the web app has no offline story, so every screen is a server
 * round-trip by design. A local database would be a second source of truth for
 * data that is stale the moment it lands.
 *
 * [studentClassId] / [teacherClassId] are the load-bearing ones — class_id is
 * required by nearly every endpoint and the server never remembers it.
 */
class Prefs(private val store: DataStore<Preferences>) {

    constructor(context: Context) : this(context.dataStore)


    private object Keys {
        val StudentClass = stringPreferencesKey("student_class_id")
        val TeacherClass = stringPreferencesKey("teacher_class_id")
        val Theme        = stringPreferencesKey("theme")          // light | dark | system
        val TeacherTab   = stringPreferencesKey("teacher_tab")    // material | rules | tools
        val StatsRange   = intPreferencesKey("stats_range")       // 7 | 30 | 0 (term)
        val TutStudent   = booleanPreferencesKey("tut_seen_student")
        val TutTeacher   = booleanPreferencesKey("tut_seen_teacher")
        val TutStats     = booleanPreferencesKey("tut_seen_stats")
        val TextSize     = stringPreferencesKey("text_size")      // normal | large
        val ReduceMotion = booleanPreferencesKey("reduce_motion")
        val Debug        = booleanPreferencesKey("debug")          // honoured only for dev accounts
        val EnterSend    = booleanPreferencesKey("enter_send")
        val LastMe       = stringPreferencesKey("last_me")        // see encodeMe
    }

    /**
     * Who was signed in last time, so a returning user skips the /auth/me wait
     * on launch. A routing hint only, never a grant: the server re-checks the
     * token and role on every call, and RootViewModel revalidates at once.
     */
    suspend fun lastMe(): Me? = decodeMe(store.data.first()[Keys.LastMe])
    suspend fun setLastMe(me: Me?) = write(Keys.LastMe, me?.let(::encodeMe))

    val textSize: Flow<String> = store.data.map { it[Keys.TextSize] ?: "normal" }
    val reduceMotion: Flow<Boolean> = store.data.map { it[Keys.ReduceMotion] ?: false }
    val debug: Flow<Boolean> = store.data.map { it[Keys.Debug] ?: false }
    // Tours default to seen: on the web they open only from Help or Settings → Replay tour.
    val tutorialSeenStats: Flow<Boolean> = store.data.map { it[Keys.TutStats] ?: true }

    suspend fun setTextSize(value: String) = write(Keys.TextSize, value)
    suspend fun setReduceMotion(on: Boolean) = store.edit { it[Keys.ReduceMotion] = on }
    suspend fun setDebug(on: Boolean) = store.edit { it[Keys.Debug] = on }
    /** On by default, as uiPref("enterSend", true) on the web. */
    val enterSend: Flow<Boolean> = store.data.map { it[Keys.EnterSend] ?: true }
    suspend fun setEnterSend(on: Boolean) = store.edit { it[Keys.EnterSend] = on }
    suspend fun setTutorialSeenStats(seen: Boolean) = store.edit { it[Keys.TutStats] = seen }

    val studentClassId: Flow<String?> = read(Keys.StudentClass)
    val teacherClassId: Flow<String?> = read(Keys.TeacherClass)
    val theme: Flow<String> = store.data.map { it[Keys.Theme] ?: "system" }
    val teacherTab: Flow<String> = store.data.map { it[Keys.TeacherTab] ?: "material" }
    val statsRange: Flow<Int> = store.data.map { it[Keys.StatsRange] ?: 0 }
    val tutorialSeenStudent: Flow<Boolean> = store.data.map { it[Keys.TutStudent] ?: true }
    val tutorialSeenTeacher: Flow<Boolean> = store.data.map { it[Keys.TutTeacher] ?: true }

    suspend fun setStudentClassId(id: String?) = write(Keys.StudentClass, id)
    suspend fun setTeacherClassId(id: String?) = write(Keys.TeacherClass, id)
    suspend fun setTheme(value: String) = write(Keys.Theme, value)
    suspend fun setTeacherTab(value: String) = write(Keys.TeacherTab, value)
    suspend fun setStatsRange(days: Int) = store.edit { it[Keys.StatsRange] = days }
    suspend fun setTutorialSeenStudent(seen: Boolean) = store.edit { it[Keys.TutStudent] = seen }
    suspend fun setTutorialSeenTeacher(seen: Boolean) = store.edit { it[Keys.TutTeacher] = seen }

    private fun read(key: Preferences.Key<String>): Flow<String?> =
        store.data.map { it[key] }

    private suspend fun write(key: Preferences.Key<String>, value: String?) {
        store.edit { prefs ->
            if (value == null) prefs.remove(key) else prefs[key] = value
        }
    }
}

internal fun encodeMe(me: Me): String = buildJsonObject {
    put("uid", JsonPrimitive(me.uid))
    me.email?.let { put("email", JsonPrimitive(it)) }
    put("role", JsonPrimitive(me.role))
    put("is_dev", JsonPrimitive(me.isDev))
}.toString()

/** Null for anything missing or unreadable, so a bad value just means "ask the server". */
internal fun decodeMe(raw: String?): Me? = runCatching {
    val o = Api.json.parseToJsonElement(raw ?: return null).jsonObject
    Me(
        uid = o["uid"]?.stringOrNull() ?: return null,
        email = o["email"]?.stringOrNull(),
        role = o["role"]?.stringOrNull() ?: return null,
        isDev = o["is_dev"]?.stringOrNull() == "true",
    )
}.getOrNull()
