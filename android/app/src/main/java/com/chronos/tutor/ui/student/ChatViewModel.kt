package com.chronos.tutor.ui.student

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.Chat
import com.chronos.tutor.data.ChatRepository
import com.chronos.tutor.data.ClassRepository
import com.chronos.tutor.data.CourseClass
import com.chronos.tutor.data.Message
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.data.ServerStatus
import com.chronos.tutor.data.gapFrom
import com.chronos.tutor.net.ApiError
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers

data class ChatUiState(
    val booting: Boolean = true,
    val classes: List<CourseClass> = emptyList(),
    val activeClassId: String? = null,
    /** Conversations for the active class only — the web filters the same way. */
    val chats: List<Chat> = emptyList(),
    val activeChatId: String? = null,
    val messages: List<Message> = emptyList(),
    val input: String = "",
    val sending: Boolean = false,
    val status: ServerStatus = ServerStatus.ONLINE,
    val error: String? = null,
    /** No courses yet: the join gate, or the teacher variant, covers the screen. */
    val needsJoin: Boolean = false,
    val isTeacher: Boolean = false,
    val joinError: String? = null,
    val joining: Boolean = false,
) {
    val activeClassName: String?
        get() = classes.firstOrNull { it.id == activeClassId }?.name
    val canSend: Boolean get() = input.isNotBlank() && !sending && activeClassId != null
}

class ChatViewModel(
    private val chatRepo: ChatRepository,
    private val classRepo: ClassRepository,
    private val prefs: Prefs,
    private val isTeacher: Boolean,
) : ViewModel() {

    private val _state = MutableStateFlow(ChatUiState(isTeacher = isTeacher))
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    /** Every chat across all classes; [ChatUiState.chats] is the filtered view. */
    private var allChats: List<Chat> = emptyList()
    private var sendJob: Job? = null

    /**
     * Bumped whenever the visible conversation changes. A reply that lands
     * after the user moved on must not write into the new view — the web uses
     * VIEW_GEN for exactly this (student.html:994).
     */
    private var viewGen = 0

    init {
        boot()
        pollHealth()
    }

    private fun boot() = viewModelScope.launch {
        _state.update { it.copy(booting = true, error = null) }
        val classes = runCatching { classRepo.list() }.getOrElse { e ->
            _state.update { it.copy(booting = false, error = e.userText()) }
            return@launch
        }
        if (classes.isEmpty()) {
            _state.update { it.copy(booting = false, needsJoin = true, classes = emptyList()) }
            return@launch
        }
        val saved = prefs.studentClassId.first()
        val active = classes.firstOrNull { it.id == saved }?.id ?: classes.first().id
        prefs.setStudentClassId(active)

        allChats = runCatching { chatRepo.listChats() }.getOrElse { e ->
            _state.update { it.copy(error = e.userText()) }
            emptyList()
        }
        _state.update { it.copy(booting = false, needsJoin = false, classes = classes, activeClassId = active) }
        openClass(active)
    }

    private fun pollHealth() = viewModelScope.launch {
        while (true) {
            var ok = chatRepo.health()
            // Cloud Run cold starts: show "waking" while retrying, like the web.
            repeat(3) {
                if (ok) return@repeat
                _state.update { it.copy(status = ServerStatus.WAKING) }
                delay(2_500)
                ok = chatRepo.health()
            }
            _state.update { it.copy(status = if (ok) ServerStatus.ONLINE else ServerStatus.OFFLINE) }
            delay(15_000)
        }
    }

    // ---- navigation -------------------------------------------------------

    fun switchClass(classId: String) = viewModelScope.launch {
        prefs.setStudentClassId(classId)
        _state.update { it.copy(activeClassId = classId, error = null) }
        openClass(classId)
    }

    private suspend fun openClass(classId: String) {
        val forClass = allChats.filter { it.classId == classId }
        val target = forClass.firstOrNull() ?: newChatFor(classId)
        _state.update { it.copy(chats = allChats.filter { c -> c.classId == classId }) }
        selectChat(target.id)
    }

    private fun newChatFor(classId: String): Chat {
        val blank = allChats.firstOrNull { it.classId == classId && it.isNew && it.messages.isEmpty() }
        if (blank != null) return blank
        val fresh = Chat(id = chatRepo.tempId(), title = "New chat", classId = classId, isNew = true, loaded = true)
        allChats = listOf(fresh) + allChats
        return fresh
    }

    fun newChat() = viewModelScope.launch {
        val classId = _state.value.activeClassId ?: return@launch
        val c = newChatFor(classId)
        _state.update { it.copy(chats = allChats.filter { x -> x.classId == classId }) }
        selectChat(c.id)
    }

    fun selectChat(chatId: String) = viewModelScope.launch { openChat(chatId) }

    private suspend fun openChat(chatId: String) {
        viewGen++
        sendJob?.cancel()
        val chat = allChats.firstOrNull { it.id == chatId } ?: return

        var loadError: String? = null
        val loaded = if (chat.loaded || chat.isNew) chat else {
            // Not marked loaded on failure, so reopening the chat retries.
            runCatching { chatRepo.loadMessages(chat.id) }.fold(
                onSuccess = { chat.copy(messages = it, loaded = true) },
                onFailure = { loadError = it.userText(); chat },
            )
        }
        replaceChat(loaded)
        _state.update {
            it.copy(
                activeChatId = loaded.id,
                messages = loaded.messages,
                input = loaded.draft,
                sending = false,
                error = loadError,
                chats = allChats.filter { c -> c.classId == loaded.classId },
            )
        }
    }

    fun deleteChat(chatId: String) = viewModelScope.launch {
        val chat = allChats.firstOrNull { it.id == chatId } ?: return@launch
        if (!chat.isNew) runCatching { chatRepo.deleteChat(chatId) }.onFailure { e ->
            _state.update { it.copy(error = e.userText()) }
            return@launch
        }
        allChats = allChats.filterNot { it.id == chatId }
        val classId = chat.classId
        _state.update { it.copy(chats = allChats.filter { c -> c.classId == classId }) }
        if (_state.value.activeChatId == chatId) openClass(classId)
    }

    // ---- composing --------------------------------------------------------

    fun setInput(value: String) {
        _state.update { it.copy(input = value) }
        val id = _state.value.activeChatId ?: return
        allChats = allChats.map { if (it.id == id) it.copy(draft = value) else it }
    }

    fun send() {
        val s = _state.value
        if (!s.canSend) return
        val chat = allChats.firstOrNull { it.id == s.activeChatId } ?: return
        val text = s.input.trim()
        val gen = viewGen

        // Optimistic student bubble plus an empty tutor bubble to stream into.
        val withStudent = chat.messages +
            Message(Message.Role.STUDENT, text) +
            Message(Message.Role.TUTOR, "", streaming = true)

        // A first message titles the chat locally at 30 chars; the server's
        // 40-char title replaces it when the reply lands.
        val localTitle = if (chat.messages.isEmpty())
            (if (text.length > 30) text.take(30) + "…" else text) else chat.title

        replaceChat(chat.copy(messages = withStudent, title = localTitle, draft = ""))
        _state.update {
            it.copy(input = "", sending = true, messages = withStudent, error = null,
                chats = allChats.filter { c -> c.classId == chat.classId })
        }

        sendJob = viewModelScope.launch {
            val buffer = StringBuilder()
            try {
                val done = chatRepo.send(text, chat.classId, chat.id) { delta ->
                    buffer.append(delta)
                    if (gen == viewGen) {
                        val snapshot = buffer.toString()
                        _state.update { st ->
                            st.copy(messages = st.messages.replaceLastTutor {
                                it.copy(content = snapshot, streaming = true)
                            })
                        }
                    }
                }

                val final = Message(
                    role = Message.Role.TUTOR,
                    content = done.response.ifBlank { buffer.toString() },
                    blocked = done.blocked,
                    reviewed = done.reviewed,
                    gap = gapFrom(done.materialGap, done.reviewed, done.blocked),
                    // Empty for students at the API level, so this only ever
                    // populates for a teacher using the tutor.
                    sources = done.sources,
                    streaming = false,
                )

                val current = allChats.firstOrNull { it.id == chat.id } ?: return@launch
                val adopted = current.copy(
                    id = if (current.isNew && done.chatId != null) done.chatId else current.id,
                    title = done.title ?: current.title,
                    isNew = false,
                    loaded = true,
                    messages = current.messages.replaceLastTutor { final },
                )
                allChats = allChats.map { if (it.id == chat.id) adopted else it }

                if (gen == viewGen) {
                    _state.update {
                        it.copy(
                            sending = false,
                            activeChatId = adopted.id,
                            messages = adopted.messages,
                            chats = allChats.filter { c -> c.classId == adopted.classId },
                        )
                    }
                }
            } catch (e: Throwable) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                val note = (e as? ApiError)?.userMessage ?: e.message
                    ?: "Can't reach the server. Please try again."
                val partial = buffer.toString()
                // Partial text is kept and the error appended under it, rather
                // than the answer being replaced by the error.
                val current = allChats.firstOrNull { it.id == chat.id }
                if (current != null) {
                    val patched = current.copy(messages = current.messages.replaceLastTutor {
                        it.copy(content = partial, streaming = false, errorNote = note)
                    })
                    allChats = allChats.map { if (it.id == chat.id) patched else it }
                    if (gen == viewGen) {
                        _state.update { it.copy(sending = false, messages = patched.messages) }
                    }
                }
                if (gen == viewGen && e is ApiError.Offline) {
                    _state.update { it.copy(status = ServerStatus.OFFLINE) }
                }
            }
        }
    }

    // ---- join gate --------------------------------------------------------

    fun join(code: String) = viewModelScope.launch {
        if (code.isBlank()) {
            _state.update { it.copy(joinError = "Enter a course code.") }; return@launch
        }
        _state.update { it.copy(joining = true, joinError = null) }
        runCatching { classRepo.join(code) }.fold(
            onSuccess = {
                _state.update { s -> s.copy(joining = false, needsJoin = false) }
                boot()
            },
            onFailure = { e ->
                _state.update {
                    it.copy(
                        joining = false,
                        joinError = (e as? ApiError)?.userMessage ?: e.message
                            ?: "That code did not work.",
                    )
                }
            },
        )
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun retry() = boot()

    private fun replaceChat(chat: Chat) {
        allChats = allChats.map { if (it.id == chat.id) chat else it }
    }
}

private fun Throwable.userText(): String =
    (this as? ApiError)?.userMessage ?: message ?: "Can't reach the server. Please try again."

/** Replaces the trailing tutor message, which is the one being streamed into. */
private inline fun List<Message>.replaceLastTutor(block: (Message) -> Message): List<Message> {
    val idx = indexOfLast { it.role == Message.Role.TUTOR }
    if (idx < 0) return this
    return toMutableList().also { it[idx] = block(it[idx]) }
}
