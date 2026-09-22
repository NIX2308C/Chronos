package com.chronos.tutor.data

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

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
class Prefs(private val context: Context) {

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
    }

    val textSize: Flow<String> = context.dataStore.data.map { it[Keys.TextSize] ?: "normal" }
    val reduceMotion: Flow<Boolean> = context.dataStore.data.map { it[Keys.ReduceMotion] ?: false }
    val debug: Flow<Boolean> = context.dataStore.data.map { it[Keys.Debug] ?: false }
    val tutorialSeenStats: Flow<Boolean> = context.dataStore.data.map { it[Keys.TutStats] ?: false }

    suspend fun setTextSize(value: String) = write(Keys.TextSize, value)
    suspend fun setReduceMotion(on: Boolean) = context.dataStore.edit { it[Keys.ReduceMotion] = on }
    suspend fun setDebug(on: Boolean) = context.dataStore.edit { it[Keys.Debug] = on }
    suspend fun setTutorialSeenStats(seen: Boolean) = context.dataStore.edit { it[Keys.TutStats] = seen }

    val studentClassId: Flow<String?> = read(Keys.StudentClass)
    val teacherClassId: Flow<String?> = read(Keys.TeacherClass)
    val theme: Flow<String> = context.dataStore.data.map { it[Keys.Theme] ?: "system" }
    val teacherTab: Flow<String> = context.dataStore.data.map { it[Keys.TeacherTab] ?: "material" }
    val statsRange: Flow<Int> = context.dataStore.data.map { it[Keys.StatsRange] ?: 0 }
    val tutorialSeenStudent: Flow<Boolean> = context.dataStore.data.map { it[Keys.TutStudent] ?: false }
    val tutorialSeenTeacher: Flow<Boolean> = context.dataStore.data.map { it[Keys.TutTeacher] ?: false }

    suspend fun setStudentClassId(id: String?) = write(Keys.StudentClass, id)
    suspend fun setTeacherClassId(id: String?) = write(Keys.TeacherClass, id)
    suspend fun setTheme(value: String) = write(Keys.Theme, value)
    suspend fun setTeacherTab(value: String) = write(Keys.TeacherTab, value)
    suspend fun setStatsRange(days: Int) = context.dataStore.edit { it[Keys.StatsRange] = days }
    suspend fun setTutorialSeenStudent(seen: Boolean) = context.dataStore.edit { it[Keys.TutStudent] = seen }
    suspend fun setTutorialSeenTeacher(seen: Boolean) = context.dataStore.edit { it[Keys.TutTeacher] = seen }

    private fun read(key: Preferences.Key<String>): Flow<String?> =
        context.dataStore.data.map { it[key] }

    private suspend fun write(key: Preferences.Key<String>, value: String?) {
        context.dataStore.edit { prefs ->
            if (value == null) prefs.remove(key) else prefs[key] = value
        }
    }
}
