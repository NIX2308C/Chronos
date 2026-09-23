package com.chronos.tutor

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.chronos.tutor.data.ChatRepository
import com.chronos.tutor.data.ClassRepository
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ChatStream
import com.chronos.tutor.ui.common.SoundPlayer
import com.chronos.tutor.ui.common.Sounds
import com.chronos.tutor.ui.student.ChatUiState
import com.chronos.tutor.ui.student.ChatViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** The student home's first load, against a fake backend. */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelBootTest {

    @get:Rule val tmp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var storeScope: CoroutineScope
    private lateinit var prefs: Prefs
    /** Path prefix → (status, body). Tests override entries before building the ViewModel. */
    private val routes = mutableMapOf(
        "/classes" to (200 to """{"classes":[{"id":"c1","name":"Bio","toolkits":["quiz"]},{"id":"c2","name":"Chem"}]}"""),
        "/chats/h1/messages" to (200 to """{"messages":[{"role":"student","content":"hi"},{"role":"teacher","content":"hello"}]}"""),
        "/chats/h2/messages" to (200 to """{"messages":[{"role":"student","content":"acids?"}]}"""),
        "/chats" to (200 to """{"chats":[{"id":"h1","title":"Cells","class_id":"c1"},{"id":"h2","title":"Acids","class_id":"c2"}]}"""),
        "/student/files" to (200 to """{"files":[{"id":"f1","name":"essay.pdf","kind":"assignment"}],"max":5}"""),
        "/health" to (200 to """{"status":"ok"}"""),
    )

    private object Silent : SoundPlayer {
        override fun play(kind: Sounds.Kind) {}
        override fun stop() {}
    }

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                // Longest prefix first, so /chats/h1/messages is not answered as /chats.
                val hit = routes.entries.sortedByDescending { it.key.length }.firstOrNull { path.startsWith(it.key) }
                    ?: return MockResponse().setResponseCode(404).setBody("""{"error":"no route"}""")
                return MockResponse().setResponseCode(hit.value.first).setBody(hit.value.second)
            }
        }
        server.start()
        storeScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        prefs = Prefs(PreferenceDataStoreFactory.create(scope = storeScope) { File(tmp.root, "test.preferences_pb") })
    }

    @After
    fun tearDown() {
        server.shutdown()
        storeScope.cancel()
        Dispatchers.resetMain()
    }

    private fun vm(): ChatViewModel {
        val api = Api(baseUrl = server.url("/").toString().trimEnd('/'), client = OkHttpClient())
        return ChatViewModel(
            chatRepo = ChatRepository(api, ChatStream(api, api.client)),
            classRepo = ClassRepository(api),
            prefs = prefs,
            isTeacher = false,
            sounds = Silent,
        )
    }

    private fun ChatViewModel.awaitState(done: (ChatUiState) -> Boolean): ChatUiState =
        runBlocking { withTimeout(10_000) { state.first { done(it) } } }

    @Test
    fun `opens the first course, its latest chat, messages and attachments`() {
        val s = vm().awaitState { !it.booting && it.messages.isNotEmpty() && it.files.isNotEmpty() }
        assertEquals(listOf("c1", "c2"), s.classes.map { it.id })
        assertEquals("c1", s.activeClassId)
        assertEquals(listOf("quiz"), s.toolkits)
        assertEquals(listOf("h1"), s.chats.map { it.id })          // only this course's chats
        assertEquals("h1", s.activeChatId)
        assertEquals(listOf("hi", "hello"), s.messages.map { it.content })
        assertEquals(listOf("essay.pdf"), s.files.map { it.name })
        assertEquals(5, s.filesMax)
    }

    @Test
    fun `remembers the course the student used last`() {
        runBlocking { prefs.setStudentClassId("c2") }
        val s = vm().awaitState { !it.booting && it.messages.isNotEmpty() }
        assertEquals("c2", s.activeClassId)
        assertEquals("h2", s.activeChatId)
    }

    @Test
    fun `no courses shows the join gate`() {
        routes["/classes"] = 200 to """{"classes":[]}"""
        val s = vm().awaitState { !it.booting }
        assertTrue(s.needsJoin)
        assertTrue(s.classes.isEmpty())
    }

    @Test
    fun `courses still open when the chat list fails`() {
        routes["/chats"] = 500 to """{"error":"boom"}"""
        val s = vm().awaitState { !it.booting && it.activeChatId != null }
        assertEquals(listOf("c1", "c2"), s.classes.map { it.id })
        assertFalse(s.needsJoin)
        assertTrue(s.activeChatId!!.startsWith("new_"))            // a fresh chat to type into
    }

    @Test
    fun `a failed course list is an error, not the join gate`() {
        routes["/classes"] = 500 to """{"error":"boom"}"""
        val s = vm().awaitState { !it.booting }
        assertFalse(s.needsJoin)
        assertTrue(s.error != null)
    }
}
