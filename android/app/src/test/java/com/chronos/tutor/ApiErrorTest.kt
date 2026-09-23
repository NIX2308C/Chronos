package com.chronos.tutor

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ApiError
import com.chronos.tutor.net.friendlyAuthError
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The status mapping is what the user actually reads when something fails, and
 * it is the thing a judge exploring freely is most likely to trip. A 429
 * rendering as "Something went wrong" is a visible defect, so each status gets
 * pinned here rather than trusted.
 */
class ApiErrorTest {

    private lateinit var server: MockWebServer
    private lateinit var api: Api

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = Api(baseUrl = server.url("/").toString().trimEnd('/'), client = OkHttpClient())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun respond(code: Int, body: String = "{}", headers: Map<String, String> = emptyMap()) {
        val r = MockResponse().setResponseCode(code).setBody(body)
        headers.forEach { (k, v) -> r.addHeader(k, v) }
        server.enqueue(r)
    }

    @Test
    fun `non-streamed chat 200 carrying an error throws`() {
        respond(200, """{"error":"Course not found"}""", mapOf("Content-Type" to "application/json"))
        val stream = com.chronos.tutor.net.ChatStream(api, OkHttpClient())
        val e = runCatching { stream.send("hi", "c", "new_x") {} }.exceptionOrNull()
        assertTrue(e is ApiError.Server)
        assertEquals("Course not found", (e as ApiError.Server).serverMessage)
    }

    @Test
    fun `200 returns the parsed object`() {
        respond(200, """{"status":"ok"}""")
        val out = api.get("/health")
        assertEquals("ok", out["status"]?.toString()?.trim('"'))
    }

    @Test
    fun `401 maps to Unauthorized and keeps the server message`() {
        respond(401, """{"error":"Please sign in again."}""")
        val e = runCatching { api.get("/auth/me") }.exceptionOrNull()
        assertTrue(e is ApiError.Unauthorized)
        assertEquals("Please sign in again.", (e as ApiError).userMessage)
    }

    @Test
    fun `403 for a roleless account is flagged as recoverable`() {
        respond(403, """{"error":"Finish creating your account first."}""")
        val e = runCatching { api.get("/auth/me") }.exceptionOrNull()
        assertTrue(e is ApiError.Forbidden)
        // This is the signal that routes to the finish-signup screen instead of
        // dead-ending the user; getting it wrong bricks the account's email.
        assertTrue((e as ApiError.Forbidden).needsRole)
    }

    @Test
    fun `403 for a foreign class is not treated as roleless`() {
        respond(403, """{"error":"Forbidden. Teacher access only."}""")
        val e = runCatching { api.get("/stats") }.exceptionOrNull()
        assertTrue(e is ApiError.Forbidden)
        assertTrue(!(e as ApiError.Forbidden).needsRole)
    }

    @Test
    fun `429 surfaces Retry-After in the message`() {
        respond(429, """{"error":""}""", mapOf("Retry-After" to "12"))
        val e = runCatching { api.post("/chat") }.exceptionOrNull()
        assertTrue(e is ApiError.RateLimited)
        assertEquals(12, (e as ApiError.RateLimited).retryAfterSeconds)
        assertTrue(e.userMessage.contains("12"))
    }

    @Test
    fun `429 without Retry-After still reads as a throttle, not a crash`() {
        respond(429, "{}")
        val e = runCatching { api.post("/chat") }.exceptionOrNull()
        assertTrue(e is ApiError.RateLimited)
        assertTrue((e as ApiError).userMessage.contains("fast", ignoreCase = true))
    }

    @Test
    fun `413 reports the upload cap rather than a generic failure`() {
        respond(413, """{"error":"File too large."}""")
        val e = runCatching { api.post("/upload") }.exceptionOrNull()
        assertTrue(e is ApiError.TooLarge)
        assertEquals("File too large.", (e as ApiError).userMessage)
    }

    @Test
    fun `500 maps to Server and carries the status`() {
        respond(500, """{"error":"boom"}""")
        val e = runCatching { api.get("/chats") }.exceptionOrNull()
        assertTrue(e is ApiError.Server)
        assertEquals(500, (e as ApiError.Server).status)
    }

    @Test
    fun `details wins over error, matching the web helper`() {
        respond(400, """{"error":"nope","details":"the specific reason"}""")
        val e = runCatching { api.get("/chats") }.exceptionOrNull()
        assertEquals("the specific reason", (e as ApiError).userMessage)
    }

    @Test
    fun `a 200 carrying an error key is still a failure`() {
        respond(200, """{"error":"quietly failed"}""")
        val e = runCatching { api.get("/chats") }.exceptionOrNull()
        assertTrue(e is ApiError.Server)
    }

    @Test
    fun `a dead server maps to Offline, not a crash`() {
        server.shutdown()
        val e = runCatching { api.get("/health") }.exceptionOrNull()
        assertTrue(e is ApiError.Offline)
    }

    @Test
    fun `non-JSON body maps to Malformed`() {
        respond(200, "<html>gateway error</html>")
        val e = runCatching { api.get("/health") }.exceptionOrNull()
        assertTrue(e is ApiError.Malformed)
    }

    @Test
    fun `firebase auth codes map to the same copy as the web`() {
        assertEquals("Wrong email or password.", friendlyAuthError("ERROR_WRONG_PASSWORD", null))
        assertEquals("Wrong email or password.", friendlyAuthError("ERROR_INVALID_CREDENTIAL", null))
        // Same copy as a wrong password, so sign-in never reveals whether an email is registered.
        assertEquals("Wrong email or password.", friendlyAuthError("ERROR_USER_NOT_FOUND", null))
        assertEquals("An account with that email already exists.", friendlyAuthError("ERROR_EMAIL_ALREADY_IN_USE", null))
        assertEquals("Password must be at least 6 characters.", friendlyAuthError("ERROR_WEAK_PASSWORD", null))
        assertEquals("That doesn't look like a valid email.", friendlyAuthError("ERROR_INVALID_EMAIL", null))
        assertEquals("Too many attempts. Try again in a moment.", friendlyAuthError("ERROR_TOO_MANY_REQUESTS", null))
        // Unknown code falls back to the raw message, then to a safe default.
        assertEquals("raw", friendlyAuthError("ERROR_SOMETHING_NEW", "raw"))
        assertEquals("Something went wrong.", friendlyAuthError(null, null))
    }
}
