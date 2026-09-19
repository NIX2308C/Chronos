package com.chronos.tutor.net

import com.chronos.tutor.BuildConfig
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The backend client.
 *
 * Deliberately not Retrofit: these routes are not REST — /rules, /stats,
 * /roster and /student-profile are POST-reads — and /chat needs a per-call
 * timeout rather than a client-wide one. A generic helper plus thin typed
 * wrappers is less code here than annotations and a converter factory.
 */
class Api(
    private val baseUrl: String = BuildConfig.API_BASE,
    val client: OkHttpClient,
) {
    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

        /**
         * /chat and /tools/run only. app.py gives Gemini 60s (GEMINI_TIMEOUT_MS),
         * so OkHttp's 10s default read timeout would break them outright. This is
         * applied per call, never globally — a client-wide 90s would leave a dead
         * /health poll hanging for a minute and a half.
         */
        val SLOW_CALL_TIMEOUT_MS = TimeUnit.SECONDS.toMillis(90)

        val json = Json {
            ignoreUnknownKeys = true   // the server adds keys; old clients must not break
            isLenient = true
            explicitNulls = false
            coerceInputValues = true
        }
    }

    fun url(path: String): String = baseUrl.trimEnd('/') + path

    /** GET returning a parsed JSON object. */
    fun get(path: String, slow: Boolean = false): JsonObject =
        execute(Request.Builder().url(url(path)).get().build(), slow)

    /** POST with a JSON body, returning a parsed JSON object. */
    fun post(path: String, body: JsonElement? = null, slow: Boolean = false): JsonObject {
        val payload = (body?.toString() ?: "{}").toRequestBody(JSON_MEDIA)
        return execute(Request.Builder().url(url(path)).post(payload).build(), slow)
    }

    /** DELETE returning a parsed JSON object. */
    fun delete(path: String): JsonObject =
        execute(Request.Builder().url(url(path)).delete().build(), false)

    /** Runs a prepared request (used by multipart uploads, which build their own body). */
    fun execute(request: Request, slow: Boolean = false): JsonObject {
        val call = client.newCall(request)
        if (slow) call.timeout().timeout(SLOW_CALL_TIMEOUT_MS, TimeUnit.MILLISECONDS)

        val response = try {
            call.execute()
        } catch (e: IOException) {
            throw ApiError.Offline(e)
        }

        response.use { return it.parseOrThrow() }
    }

    /**
     * Status mapping ported from apiJson() in web/auth.js:100-115, with 429 and
     * 413 split out — app.py rate-limits six routes and returns a JSON 413 for
     * oversized uploads, and both deserve their own copy.
     */
    private fun Response.parseOrThrow(): JsonObject {
        val raw = body?.string().orEmpty()
        val parsed: JsonObject? = runCatching {
            json.parseToJsonElement(raw) as? JsonObject
        }.getOrNull()

        // takeIf { isNotBlank() }: app.py can answer {"error":""}, and a blank
        // message winning over the generated one would render an empty error.
        val serverMessage = parsed?.get("details")?.stringOrNull()?.takeIf { it.isNotBlank() }
            ?: parsed?.get("error")?.stringOrNull()?.takeIf { it.isNotBlank() }

        when (code) {
            401 -> throw ApiError.Unauthorized(serverMessage)
            403 -> throw ApiError.Forbidden(serverMessage)
            413 -> throw ApiError.TooLarge(serverMessage)
            429 -> throw ApiError.RateLimited(
                retryAfterSeconds = header("Retry-After")?.toIntOrNull(),
                serverMessage = serverMessage,
            )
        }
        if (!isSuccessful) throw ApiError.Server(code, serverMessage)

        val obj = parsed
            ?: throw ApiError.Malformed("expected a JSON object, got ${raw.take(120)}")

        // A 2xx carrying {"error": ...} is still a failure — the web treats it
        // the same way, and several app.py routes answer that shape.
        if (serverMessage != null && obj.containsKey("error")) {
            throw ApiError.Server(code, serverMessage)
        }
        return obj
    }
}

internal fun JsonElement.stringOrNull(): String? =
    runCatching { jsonPrimitive.content }.getOrNull()
