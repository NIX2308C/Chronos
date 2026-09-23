package com.chronos.tutor.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.ChatRepository
import com.chronos.tutor.data.Personality
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.data.PublicStatus
import com.chronos.tutor.data.SettingsRepository
import com.chronos.tutor.data.TutorPrefs
import com.chronos.tutor.net.ApiError
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class SettingsUiState(
    val prefs: TutorPrefs? = null,
    val personalities: List<Personality> = emptyList(),
    val saved: String? = null,           // "Saved" / error line under the tutor section
    val clearing: Boolean = false,
    val deployment: List<Pair<String, String>>? = null,
    val status: PublicStatus? = null,
    val statusError: String? = null,
)

class SettingsViewModel(
    private val repo: SettingsRepository,
    private val chatRepo: ChatRepository,
    val prefsStore: Prefs,
) : ViewModel() {

    private val _state = MutableStateFlow(SettingsUiState())
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()
    private var saveJob: Job? = null

    fun loadTutor() = viewModelScope.launch {
        runCatching { repo.preferences() }.fold(
            onSuccess = { (p, list) -> _state.update { it.copy(prefs = p, personalities = list) } },
            onFailure = { e -> _state.update { it.copy(saved = e.text()) } },
        )
    }

    /** Debounced like queueSave() in web/settings.js, so typing a style note is one request. */
    fun updateTutor(next: TutorPrefs) {
        _state.update { it.copy(prefs = next, saved = null) }
        saveJob?.cancel()
        saveJob = viewModelScope.launch {
            delay(350)
            runCatching { repo.savePreferences(next) }.fold(
                // Keep what the user is typing; only the confirmation comes back.
                onSuccess = { _state.update { it.copy(saved = "Saved") } },
                onFailure = { e -> _state.update { it.copy(saved = e.text()) } },
            )
        }
    }

    fun setTheme(v: String) = viewModelScope.launch { prefsStore.setTheme(v) }
    fun setTextSize(v: String) = viewModelScope.launch { prefsStore.setTextSize(v) }
    fun setReduceMotion(v: Boolean) = viewModelScope.launch { prefsStore.setReduceMotion(v) }
    fun setDebug(v: Boolean) = viewModelScope.launch { prefsStore.setDebug(v) }
    fun setEnterSend(v: Boolean) = viewModelScope.launch { prefsStore.setEnterSend(v) }

    /** "Replay tour": the home screen shows it again when the user goes back. */
    fun replayTour(teacher: Boolean) = viewModelScope.launch {
        if (teacher) { prefsStore.setTutorialSeenTeacher(false); prefsStore.setTutorialSeenStats(false) }
        else prefsStore.setTutorialSeenStudent(false)
    }

    /** Every saved conversation in every course, one DELETE each, as clearAllChats() on the web. */
    fun deleteAllChats(onDone: (String) -> Unit) = viewModelScope.launch {
        _state.update { it.copy(clearing = true) }
        val result = runCatching {
            val chats = chatRepo.listChats()
            var failed = 0
            chats.forEach { c -> if (runCatching { chatRepo.deleteChat(c.id) }.isFailure) failed++ }
            if (failed == 0) "Deleted" else "Couldn't delete everything"
        }.getOrElse { it.text() }
        _state.update { it.copy(clearing = false) }
        onDone(result)
    }

    fun loadDeployment() = viewModelScope.launch {
        runCatching { repo.deployment() }.onSuccess { d -> _state.update { it.copy(deployment = d) } }
            .onFailure { e -> _state.update { it.copy(deployment = listOf("error" to e.text())) } }
    }

    /** Polled every 30s while the status screen is open, like web/status.html. */
    fun watchStatus() = viewModelScope.launch {
        while (isActive) {
            runCatching { repo.publicStatus() }.fold(
                onSuccess = { s -> _state.update { it.copy(status = s, statusError = null) } },
                onFailure = { e -> _state.update { it.copy(statusError = e.text()) } },
            )
            delay(30_000)
        }
    }
}

private fun Throwable.text() = (this as? ApiError)?.userMessage ?: message ?: "Something went wrong."
