package com.chronos.tutor

import com.chronos.tutor.data.AuthSession
import com.chronos.tutor.data.AuthState
import com.chronos.tutor.data.Me
import com.chronos.tutor.ui.RootViewModel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.IOException

/** Where the app opens: cached identity first, then whatever the server says. */
@OptIn(ExperimentalCoroutinesApi::class)
class RootViewModelTest {

    private val me = Me(uid = "u1", email = "a@b.c", role = "student")

    private class FakeSession(private val cachedMe: Me?) : AuthSession {
        val server = CompletableDeferred<AuthState>()
        var signOuts = 0
        override suspend fun cached() = cachedMe
        override suspend fun resolve() = server.await()
        override suspend fun signOut() { signOuts++ }
    }

    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm(session: AuthSession, events: MutableSharedFlow<Unit> = MutableSharedFlow(extraBufferCapacity = 1)) =
        RootViewModel(session, events)

    @Test
    fun `cached identity opens at once, then the server's answer replaces it`() {
        val session = FakeSession(me)
        val root = vm(session)
        assertEquals(AuthState.Ready(me), root.state.value)   // before the server answers

        val teacher = me.copy(role = "teacher")
        session.server.complete(AuthState.Ready(teacher))
        assertEquals(AuthState.Ready(teacher), root.state.value)
    }

    @Test
    fun `offline keeps the cached identity`() {
        val session = FakeSession(me)
        val root = vm(session)
        session.server.completeExceptionally(IOException("offline"))
        assertEquals(AuthState.Ready(me), root.state.value)
    }

    @Test
    fun `server saying signed out wins over the cache`() {
        val session = FakeSession(me)
        val root = vm(session)
        session.server.complete(AuthState.SignedOut)
        assertEquals(AuthState.SignedOut, root.state.value)
    }

    @Test
    fun `no cache waits on the server, and failure means signed out`() {
        val session = FakeSession(null)
        val root = vm(session)
        assertEquals(AuthState.Loading, root.state.value)
        session.server.completeExceptionally(IOException("offline"))
        assertEquals(AuthState.SignedOut, root.state.value)
    }

    @Test
    fun `a session the server rejects is signed out locally`() {
        val session = FakeSession(me)
        val events = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
        val root = vm(session, events)
        session.server.complete(AuthState.Ready(me))

        events.tryEmit(Unit)
        assertEquals(1, session.signOuts)
        assertEquals(AuthState.SignedOut, root.state.value)
    }

    @Test
    fun `no Firebase config shows the unconfigured state`() {
        val root = RootViewModel(null, MutableSharedFlow(), configError = "missing app id")
        assertEquals(AuthState.Unconfigured("missing app id"), root.state.value)
    }
}
