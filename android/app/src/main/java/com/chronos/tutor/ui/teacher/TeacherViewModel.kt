package com.chronos.tutor.ui.teacher

import android.content.ContentResolver
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.ClassRepository
import com.chronos.tutor.data.CourseClass
import com.chronos.tutor.data.CourseDoc
import com.chronos.tutor.data.CourseSettings
import com.chronos.tutor.data.CustomRule
import com.chronos.tutor.data.Material
import com.chronos.tutor.data.Member
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.data.Stats
import com.chronos.tutor.data.StudentProfile
import com.chronos.tutor.data.TeacherRepository
import com.chronos.tutor.net.ApiError
import com.chronos.tutor.net.ChatDone
import com.chronos.tutor.ui.common.readUri
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class TeacherSection { MATERIAL, ANALYTICS }

data class Notice(val text: String, val error: Boolean)

data class TeacherUiState(
    val booting: Boolean = true,
    val bootError: String? = null,
    val classes: List<CourseClass> = emptyList(),
    val activeClassId: String? = null,
    val section: TeacherSection = TeacherSection.MATERIAL,
    val tab: String = "material",              // material | rules | tools
    val notice: Notice? = null,

    val material: Material? = null,
    val materialLoading: Boolean = false,
    val uploading: String? = null,             // file name while an upload runs
    val settings: CourseSettings? = null,

    val probing: Boolean = false,
    val probeResult: ChatDone? = null,

    val range: Int = 0,                        // 7 | 30 | 0 (term)
    val stats: Stats? = null,
    val statsLoading: Boolean = false,
    val roster: List<Member> = emptyList(),
    val profileFor: Member? = null,
    val profile: StudentProfile? = null,
) {
    val active: CourseClass? get() = classes.firstOrNull { it.id == activeClassId }
}

class TeacherViewModel(
    private val repo: TeacherRepository,
    private val classRepo: ClassRepository,
    private val prefs: Prefs,
) : ViewModel() {

    private val _state = MutableStateFlow(TeacherUiState())
    val state: StateFlow<TeacherUiState> = _state.asStateFlow()
    private var settingsSave: Job? = null

    /** A reply for a course the teacher has since left must not land in the new one (knowledgeGen on the web). */
    private fun stillOn(classId: String) = _state.value.activeClassId == classId

    init { boot() }

    fun boot() = viewModelScope.launch {
        _state.update { it.copy(booting = true, bootError = null) }
        val classes = runCatching { classRepo.list() }.getOrElse { e ->
            _state.update { it.copy(booting = false, bootError = e.text()) }; return@launch
        }
        val saved = prefs.teacherClassId.first()
        val active = classes.firstOrNull { it.id == saved }?.id ?: classes.firstOrNull()?.id
        _state.update {
            it.copy(
                booting = false, classes = classes, activeClassId = active,
                tab = prefs.teacherTab.first(), range = prefs.statsRange.first(),
            )
        }
        if (active != null) loadCourse()
    }

    fun dismissNotice() = _state.update { it.copy(notice = null) }
    private fun say(text: String, error: Boolean = false) = _state.update { it.copy(notice = Notice(text, error)) }

    // ---- courses ----------------------------------------------------------

    fun selectCourse(id: String) = viewModelScope.launch {
        prefs.setTeacherClassId(id)
        _state.update {
            it.copy(activeClassId = id, material = null, settings = null, stats = null,
                roster = emptyList(), probeResult = null)
        }
        loadCourse()
    }

    fun createCourse(name: String) = viewModelScope.launch {
        if (name.isBlank()) return@launch
        runCatching { repo.createCourse(name) }.fold(
            onSuccess = { c ->
                _state.update { it.copy(classes = it.classes + c) }
                say("Course created. Join code: ${c.joinCode.orEmpty()}")
                selectCourse(c.id)
            },
            onFailure = { say(it.text(), true) },
        )
    }

    fun deleteCourse(id: String) = viewModelScope.launch {
        runCatching { repo.deleteCourse(id) }.fold(
            onSuccess = {
                val rest = _state.value.classes.filterNot { it.id == id }
                _state.update { it.copy(classes = rest) }
                say("Course deleted")
                val next = rest.firstOrNull()?.id
                if (next != null) selectCourse(next) else {
                    prefs.setTeacherClassId(null)
                    _state.update { it.copy(activeClassId = null, material = null, settings = null, stats = null) }
                }
            },
            onFailure = { say(it.text(), true) },
        )
    }

    // ---- navigation -------------------------------------------------------

    fun setSection(s: TeacherSection) {
        _state.update { it.copy(section = s) }
        if (s == TeacherSection.ANALYTICS && _state.value.stats == null) loadAnalytics()
    }

    fun setTab(tab: String) = viewModelScope.launch {
        prefs.setTeacherTab(tab); _state.update { it.copy(tab = tab) }
    }

    private fun loadCourse() {
        loadMaterial()
        loadSettings()
        if (_state.value.section == TeacherSection.ANALYTICS) loadAnalytics()
    }

    // ---- material & rules -------------------------------------------------

    fun loadMaterial() = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        _state.update { it.copy(materialLoading = true) }
        runCatching { repo.material(id) }.fold(
            onSuccess = { m -> if (stillOn(id)) _state.update { it.copy(material = m, materialLoading = false) } },
            onFailure = { e -> if (stillOn(id)) { _state.update { it.copy(materialLoading = false) }; say(e.text(), true) } },
        )
    }

    private fun loadSettings() = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        runCatching { repo.settings(id) }.fold(
            onSuccess = { s -> if (stillOn(id)) _state.update { it.copy(settings = s) } },
            onFailure = { if (stillOn(id)) say(it.text(), true) },
        )
    }

    /**
     * Optimistic, and batched like autosave() on the web: a quick run of toggles
     * is one save of the latest state, so replies can't land out of order. On
     * failure the server's copy is reloaded.
     */
    fun updateSettings(next: CourseSettings) {
        val id = _state.value.activeClassId ?: return
        _state.update { it.copy(settings = next) }
        settingsSave?.cancel()
        settingsSave = viewModelScope.launch {
            delay(300)
            runCatching { repo.saveSettings(id, next) }.fold(
                onSuccess = { saved -> if (stillOn(id)) _state.update { it.copy(settings = saved) } },
                onFailure = { e -> say(e.text(), true); if (stillOn(id)) loadSettings() },
            )
        }
    }

    /** One rule per line, as the web's bulk add. */
    fun addRules(text: String) = saveRules(
        text.lines().map(::flat).filter { it.isNotEmpty() }.map { CustomRule("", it) },
        done = "Rule saved",
    )

    /** A rule is one line; unchanged text is not re-saved (ruleEditor on the web). */
    fun editRule(rule: CustomRule, text: String) {
        val t = flat(text)
        if (t.isEmpty() || t == flat(rule.text)) return
        saveRules(listOf(rule.copy(text = t)), done = "Rule updated")
    }

    private fun saveRules(rules: List<CustomRule>, done: String) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        if (rules.isEmpty()) return@launch
        runCatching { repo.saveRules(id, rules) }.fold(
            onSuccess = { say(if (rules.size > 1) "${rules.size} rules saved" else done); loadMaterial() },
            onFailure = { say(it.text(), true) },
        )
    }

    fun deleteRule(ruleId: String) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        runCatching { repo.deleteRule(id, ruleId) }.fold(
            onSuccess = { say("Rule deleted"); loadMaterial() },
            onFailure = { say(it.text(), true) },
        )
    }

    fun deleteDoc(doc: CourseDoc) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        runCatching { repo.deleteDoc(id, doc.base) }.fold(
            onSuccess = { say("Document deleted"); loadMaterial() },
            onFailure = { say(it.text(), true) },
        )
    }

    fun upload(resolver: ContentResolver, uri: Uri) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        if (_state.value.uploading != null) { say("One upload at a time.", true); return@launch }
        val (name, mime, bytes) = runCatching { readUri(resolver, uri) }.getOrElse {
            say("Couldn't read that file.", true); return@launch
        }
        _state.update { it.copy(uploading = name) }
        runCatching { repo.upload(id, name, mime, bytes) }.fold(
            onSuccess = { warning ->
                _state.update { it.copy(uploading = null) }
                say(warning ?: "\"$name\" added to the course", error = warning != null)
                loadMaterial()
            },
            onFailure = { e -> _state.update { it.copy(uploading = null) }; say(e.text(), true) },
        )
    }

    // ---- course check -----------------------------------------------------

    fun probe(question: String) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        if (question.isBlank()) return@launch
        _state.update { it.copy(probing = true, probeResult = null) }
        runCatching { repo.probe(id, question.trim()) }.fold(
            onSuccess = { r -> _state.update { it.copy(probing = false, probeResult = r) } },
            onFailure = { e -> _state.update { it.copy(probing = false) }; say(e.text(), true) },
        )
    }

    // ---- analytics --------------------------------------------------------

    fun setRange(days: Int) = viewModelScope.launch {
        prefs.setStatsRange(days)
        _state.update { it.copy(range = days) }
        loadAnalytics()
    }

    fun loadAnalytics() = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        val days = _state.value.range
        // Only the reply for what is on screen now counts; a quick range or course switch leaves older ones behind.
        fun current() = stillOn(id) && _state.value.range == days
        _state.update { it.copy(statsLoading = true) }
        launch {
            runCatching { repo.roster(id) }.onSuccess { r -> if (stillOn(id)) _state.update { it.copy(roster = r) } }
        }
        runCatching { repo.stats(id, days) }.fold(
            onSuccess = { s -> if (current()) _state.update { it.copy(stats = s, statsLoading = false) } },
            onFailure = { e -> if (current()) { _state.update { it.copy(statsLoading = false) }; say(e.text(), true) } },
        )
    }

    fun openProfile(m: Member) = viewModelScope.launch {
        val id = _state.value.activeClassId ?: return@launch
        _state.update { it.copy(profileFor = m, profile = null) }
        runCatching { repo.profile(id, m.uid) }.fold(
            onSuccess = { p -> _state.update { if (it.profileFor == m && it.activeClassId == id) it.copy(profile = p) else it } },
            onFailure = { e -> _state.update { it.copy(profileFor = null) }; say(e.text(), true) },
        )
    }

    fun closeProfile() = _state.update { it.copy(profileFor = null, profile = null) }
}

private fun flat(s: String) = s.replace(Regex("\\s+"), " ").trim()

internal fun Throwable.text(): String =
    (this as? ApiError)?.userMessage ?: message ?: "Something went wrong. Please try again."
