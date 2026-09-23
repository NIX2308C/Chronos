package com.chronos.tutor.ui.student

import android.content.ContentResolver
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.Chat
import com.chronos.tutor.data.ChatRepository
import com.chronos.tutor.data.ClassRepository
import com.chronos.tutor.data.CourseClass
import com.chronos.tutor.data.DEFAULT_FILES_MAX
import com.chronos.tutor.data.Message
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.data.ServerStatus
import com.chronos.tutor.data.StudentFile
import com.chronos.tutor.data.ToolRequest
import com.chronos.tutor.data.ToolStatus
import com.chronos.tutor.data.gapFrom
import com.chronos.tutor.data.toolLabel
import com.chronos.tutor.ui.common.Sounds
import com.chronos.tutor.ui.common.readUri
import com.chronos.tutor.net.ApiError
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
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
    /** Activity types the active course has switched on. */
    val toolkits: List<String> = emptyList(),
    val toolRunning: Boolean = false,
    /** The active chat's attachments. */
    val files: List<StudentFile> = emptyList(),
    val filesMax: Int = DEFAULT_FILES_MAX,
    val attaching: Boolean = false,
    /** Developer account with debug mode on. */
    val debug: Boolean = false,
    val enterSend: Boolean = true,
    /** "Join another course" from the drawer; closable, unlike [needsJoin]. */
    val joinOpen: Boolean = false,
) {
    val activeClassName: String?
        get() = classes.firstOrNull { it.id == activeClassId }?.name
    val busy: Boolean get() = sending || toolRunning
    val canSend: Boolean get() = input.isNotBlank() && !busy && activeClassId != null
}

class ChatViewModel(
    private val chatRepo: ChatRepository,
    private val classRepo: ClassRepository,
    private val prefs: Prefs,
    private val isTeacher: Boolean,
    private val sounds: Sounds,
    private val isDev: Boolean = false,
) : ViewModel() {

    private val _state = MutableStateFlow(ChatUiState(isTeacher = isTeacher))
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    /** Every chat across all classes; [ChatUiState.chats] is the filtered view. */
    private var allChats: List<Chat> = emptyList()
    /** Attachments per chat, so switching back does not refetch. Unsaved chats start empty. */
    private val filesByChat = mutableMapOf<String, List<StudentFile>>()
    private var toolJob: Job? = null
    private var toolChatId: String? = null

    init {
        boot()
        pollHealth()
        viewModelScope.launch { prefs.debug.collect { on -> _state.update { it.copy(debug = isDev && on) } } }
        viewModelScope.launch { prefs.enterSend.collect { on -> _state.update { it.copy(enterSend = on) } } }
    }

    private fun boot() = viewModelScope.launch {
        _state.update { it.copy(booting = true, error = null) }
        // Courses and chats are independent requests, so they go out together
        // rather than costing two round trips back to back.
        val chatsCall = async { runCatching { chatRepo.listChats() } }
        val classes = runCatching { classRepo.list() }.getOrElse { e ->
            chatsCall.cancel()
            _state.update { it.copy(booting = false, error = e.userText()) }
            return@launch
        }
        if (classes.isEmpty()) {
            chatsCall.cancel()
            _state.update { it.copy(booting = false, needsJoin = true, classes = emptyList()) }
            return@launch
        }
        val saved = prefs.studentClassId.first()
        val active = classes.firstOrNull { it.id == saved }?.id ?: classes.first().id
        prefs.setStudentClassId(active)

        allChats = chatsCall.await().getOrElse { e ->
            _state.update { it.copy(error = e.userText()) }
            emptyList()
        }
        _state.update {
            it.copy(booting = false, needsJoin = false, classes = classes, activeClassId = active,
                toolkits = classes.first { c -> c.id == active }.toolkits)
        }
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
        _state.update {
            it.copy(activeClassId = classId, error = null,
                toolkits = it.classes.firstOrNull { c -> c.id == classId }?.toolkits ?: emptyList())
        }
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
        stopTool()
        val chat = allChats.firstOrNull { it.id == chatId } ?: return

        // Attachments load alongside the messages, not after them.
        if (!chat.isNew && chat.id !in filesByChat) loadFiles(chat)

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
                // A reply still streaming in the background keeps this chat busy.
                sending = loaded.messages.lastOrNull()?.streaming == true,
                error = loadError,
                chats = allChats.filter { c -> c.classId == loaded.classId },
                files = filesByChat[loaded.id].orEmpty(),
            )
        }
    }

    // ---- attachments ------------------------------------------------------

    private fun loadFiles(chat: Chat) = viewModelScope.launch {
        runCatching { chatRepo.listFiles(chat.classId, chat.id) }.onSuccess { (files, max) ->
            filesByChat[chat.id] = files
            // This can land before openChat marks the chat active; openChat then
            // reads filesByChat. The limit is not per chat, so it always applies.
            _state.update { it.copy(filesMax = max, files = if (it.activeChatId == chat.id) files else it.files) }
        }
    }

    fun attach(resolver: ContentResolver, uri: Uri, kind: String) = viewModelScope.launch {
        val s = _state.value
        val chat = allChats.firstOrNull { it.id == s.activeChatId } ?: return@launch
        if (s.attaching) return@launch
        val (name, mime, bytes) = runCatching { readUri(resolver, uri) }.getOrElse {
            _state.update { it.copy(error = "Couldn't read that file.") }; return@launch
        }
        _state.update { it.copy(attaching = true) }
        runCatching { chatRepo.addFile(chat.classId, chat.id, kind, name, mime, bytes) }.fold(
            onSuccess = { (file, warning) ->
                filesByChat[chat.id] = filesByChat[chat.id].orEmpty() + file
                _state.update {
                    it.copy(attaching = false, error = warning,
                        files = if (it.activeChatId == chat.id) filesByChat[chat.id].orEmpty() else it.files)
                }
            },
            onFailure = { e -> _state.update { it.copy(attaching = false, error = e.userText()) } },
        )
    }

    fun removeFile(file: StudentFile) = viewModelScope.launch {
        val chatId = _state.value.activeChatId ?: return@launch
        runCatching { chatRepo.deleteFile(file.id) }.fold(
            onSuccess = {
                filesByChat[chatId] = filesByChat[chatId].orEmpty() - file
                if (_state.value.activeChatId == chatId) _state.update { it.copy(files = filesByChat[chatId].orEmpty()) }
            },
            onFailure = { e -> _state.update { it.copy(error = e.userText()) } },
        )
    }

    fun deleteChat(chatId: String) = viewModelScope.launch {
        val chat = allChats.firstOrNull { it.id == chatId } ?: return@launch
        if (!chat.isNew) runCatching { chatRepo.deleteChat(chatId) }.onFailure { e ->
            _state.update { it.copy(error = e.userText()) }
            return@launch
        }
        // An unsaved draft has no server chat to cascade from, so its files go one by one.
        if (chat.isNew) filesByChat[chatId].orEmpty().forEach { f -> runCatching { chatRepo.deleteFile(f.id) } }
        filesByChat.remove(chatId)
        allChats = allChats.filterNot { it.id == chatId }
        val classId = chat.classId
        _state.update { it.copy(chats = allChats.filter { c -> c.classId == classId }) }
        if (_state.value.activeChatId == chatId) openClass(classId)
    }

    /** After Settings deleted conversations: keep unsaved drafts, drop what the server no longer has. */
    fun reloadChats() = viewModelScope.launch {
        val fresh = runCatching { chatRepo.listChats() }.getOrElse { e ->
            _state.update { it.copy(error = e.userText()) }; return@launch
        }
        allChats = allChats.filter { it.isNew } + fresh.map { f -> allChats.firstOrNull { it.id == f.id } ?: f }
        _state.value.activeClassId?.let { openClass(it) }
    }

    // ---- composing --------------------------------------------------------

    fun setInput(value: String) {
        _state.update { it.copy(input = value) }
        val id = _state.value.activeChatId ?: return
        allChats = allChats.map { if (it.id == id) it.copy(draft = value) else it }
    }

    fun send() { send(requestedTool = null) }

    /** Null when nothing was sent; otherwise completes true once the reply has landed. */
    private fun send(requestedTool: ToolRequest?): Deferred<Boolean>? {
        val s = _state.value
        if (!s.canSend) return null
        val chat = allChats.firstOrNull { it.id == s.activeChatId } ?: return null
        val text = s.input.trim()

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

        return viewModelScope.async {
            val buffer = StringBuilder()
            // The reply keeps going when the student switches chats, as on the
            // web; it is only drawn while its chat is the one on screen.
            fun visible() = _state.value.activeChatId == chat.id
            try {
                val debug = isDev && prefs.debug.first()
                val done = chatRepo.send(text, chat.classId, chat.id, debug) { delta ->
                    buffer.append(delta)
                    if (visible()) {
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
                    debug = done.debug?.let { PRETTY.encodeToString(JsonObject.serializer(), it) },
                )

                val current = allChats.firstOrNull { it.id == chat.id } ?: return@async true
                val adopted = current.copy(
                    id = if (current.isNew && done.chatId != null) done.chatId else current.id,
                    title = done.title ?: current.title,
                    isNew = false,
                    loaded = true,
                    messages = current.messages.replaceLastTutor { final },
                )
                allChats = allChats.map { if (it.id == chat.id) adopted else it }
                // A reply got through, so the server is up (student.html:1674).
                _state.update { it.copy(status = ServerStatus.ONLINE) }

                if (visible()) {
                    _state.update {
                        it.copy(
                            sending = false,
                            activeChatId = adopted.id,
                            messages = adopted.messages,
                            chats = allChats.filter { c -> c.classId == adopted.classId },
                            // Only a reply that carries the list changes it (student.html:1691).
                            toolkits = done.toolkits ?: it.toolkits,
                        )
                    }
                    val tool = done.toolRequest ?: requestedTool?.takeIf { !done.blocked }
                    if (tool != null) runTool(tool, adopted.id, adopted.classId)
                }
                true
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
                    if (visible()) {
                        _state.update { it.copy(sending = false, messages = patched.messages) }
                    }
                }
                if (visible() && e is ApiError.Offline) {
                    _state.update { it.copy(status = ServerStatus.OFFLINE) }
                }
                false
            }
        }
    }

    // ---- learning tools ---------------------------------------------------

    /** The ✨ menu (invokeTool in web/student.html). */
    fun invokeTool(kind: String) {
        val s = _state.value
        if (s.busy || kind !in s.toolkits) return
        val chat = allChats.firstOrNull { it.id == s.activeChatId } ?: return
        val lastAsked = chat.messages.lastOrNull { it.role == Message.Role.STUDENT && it.content.isNotBlank() }
            ?.content?.take(300)
        val topic = s.input.trim().ifEmpty { null } ?: lastAsked ?: s.activeClassName ?: "this course"
        val req = ToolRequest(kind, topic)
        if (chat.isNew) {
            // /tools/run needs a saved chat, so a new one starts with a message.
            setInput("Make a ${toolLabel(kind).lowercase()} about $topic.")
            send(req)
        } else {
            runTool(req, chat.id, chat.classId)
        }
    }

    /** "Review with AI" at the end of a quiz. True only once the review was actually answered. */
    suspend fun sendText(text: String): Boolean {
        if (_state.value.busy) return false
        setInput(text)
        return send(requestedTool = null)?.await() ?: false
    }

    private fun runTool(req: ToolRequest, chatId: String, classId: String) {
        if (_state.value.toolRunning) return
        toolChatId = chatId
        patchChat(chatId) {
            it + Message(Message.Role.TUTOR, "",
                toolStatus = ToolStatus(ToolStatus.State.USING, "Creating ${toolLabel(req.type).lowercase()}…"))
        }
        _state.update { it.copy(toolRunning = true) }
        sounds.play(Sounds.Kind.USING)
        toolJob = viewModelScope.launch {
            try {
                val tool = chatRepo.runTool(classId, chatId, req)
                patchChat(chatId) { it.replaceStatus(Message(Message.Role.TUTOR, "", tool = tool)) }
                sounds.play(Sounds.Kind.DONE)
            } catch (e: Throwable) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                patchChat(chatId) {
                    it.replaceStatus(Message(Message.Role.TUTOR, "",
                        toolStatus = ToolStatus(ToolStatus.State.FAILED, e.userText())))
                }
                sounds.play(Sounds.Kind.FAILED)
            } finally {
                if (toolChatId == chatId) { toolChatId = null; _state.update { it.copy(toolRunning = false) } }
            }
        }
    }

    /** Stop button, and leaving the chat mid-run. The UI updates now; the request is abandoned. */
    fun stopTool() {
        val chatId = toolChatId ?: return
        toolChatId = null
        toolJob?.cancel()
        sounds.stop()
        patchChat(chatId) {
            it.replaceStatus(Message(Message.Role.TUTOR, "",
                toolStatus = ToolStatus(ToolStatus.State.CANCELED, "Stopped on this device.")))
        }
        _state.update { it.copy(toolRunning = false) }
    }

    fun playSound(kind: Sounds.Kind) = sounds.play(kind)

    private fun patchChat(chatId: String, block: (List<Message>) -> List<Message>) {
        val chat = allChats.firstOrNull { it.id == chatId } ?: return
        val updated = chat.copy(messages = block(chat.messages))
        replaceChat(updated)
        if (_state.value.activeChatId == chatId) _state.update { it.copy(messages = updated.messages) }
    }

    override fun onCleared() = sounds.stop()

    // ---- join gate --------------------------------------------------------

    fun join(code: String) = viewModelScope.launch {
        if (code.isBlank()) {
            _state.update { it.copy(joinError = "Enter a course code.") }; return@launch
        }
        _state.update { it.copy(joining = true, joinError = null) }
        runCatching { classRepo.join(code) }.fold(
            onSuccess = { joined ->
                // Land in the course just joined, as the web does (student.html:2004).
                if (joined.id.isNotEmpty()) prefs.setStudentClassId(joined.id)
                _state.update { s -> s.copy(joining = false, needsJoin = false, joinOpen = false) }
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

    fun openJoin() = _state.update { it.copy(joinOpen = true, joinError = null) }
    fun closeJoin() = _state.update { it.copy(joinOpen = false, joinError = null) }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun retry() = boot()

    private fun replaceChat(chat: Chat) {
        allChats = allChats.map { if (it.id == chat.id) chat else it }
    }
}

private val PRETTY = Json { prettyPrint = true }

private fun Throwable.userText(): String =
    (this as? ApiError)?.userMessage ?: message ?: "Can't reach the server. Please try again."

/** Replaces the running tool's progress line. */
private fun List<Message>.replaceStatus(with: Message): List<Message> {
    val idx = indexOfLast { it.toolStatus?.state == ToolStatus.State.USING }
    return if (idx < 0) this else toMutableList().also { it[idx] = with }
}

/** Replaces the trailing tutor message, which is the one being streamed into. */
private inline fun List<Message>.replaceLastTutor(block: (Message) -> Message): List<Message> {
    val idx = indexOfLast { it.role == Message.Role.TUTOR }
    if (idx < 0) return this
    return toMutableList().also { it[idx] = block(it[idx]) }
}
