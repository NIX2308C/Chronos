package com.chronos.tutor.net

import com.chronos.tutor.data.ToolRequest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** The `done` payload of a /chat exchange. Mirrors finish_exchange (app.py:1949-1962). */
data class ChatDone(
    val response: String,
    val blocked: Boolean,
    val reviewed: Boolean,
    val materialGap: Boolean,
    val sources: List<String>,
    val toolkits: List<String>,
    val toolRequest: ToolRequest?,
    val chatId: String?,
    val title: String?,
)

/**
 * The streaming /chat call.
 *
 * app.py answers `text/event-stream` when the body carries `stream: true`
 * (a strict identity check — the string "true" does not stream). Frames are
 * `data: {json}` separated by a blank line, with no `event:` lines, no ids and
 * no [DONE] sentinel. Three types: delta / done / error.
 *
 * Framing is parsed by hand rather than with okhttp-sse so the behaviour can
 * match readChatResponse (web/student.html:955-988) exactly — in particular
 * accepting both `data:{x}` and `data: {x}`, and treating an `error` frame as
 * an immediate abort that still keeps whatever text already arrived.
 */
class ChatStream(private val api: Api, client: OkHttpClient) {

    // A stream sits idle while Gemini thinks, so a read timeout would kill it
    // mid-answer. The overall call timeout is the real ceiling instead.
    private val streamClient: OkHttpClient = client.newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    /**
     * Streams one reply. [onDelta] is called on a background thread for each
     * chunk; the caller is responsible for hopping to the UI.
     *
     * @throws ApiError on transport, HTTP or server-signalled failure.
     */
    fun send(
        message: String,
        classId: String,
        chatId: String,
        onDelta: (String) -> Unit,
    ): ChatDone {
        // `stream` must be a real JSON true: app.py:1967 tests `is True`, so the
        // string "true" would silently fall back to a non-streaming reply.
        val payload = buildJsonObject {
            put("message", JsonPrimitive(message))
            put("class_id", JsonPrimitive(classId))
            put("chat_id", JsonPrimitive(chatId))
            put("stream", JsonPrimitive(true))
        }.toString()

        val request = Request.Builder()
            .url(api.url("/chat"))
            .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()

        val call = streamClient.newCall(request)
        call.timeout().timeout(180, TimeUnit.SECONDS)

        val response = try {
            call.execute()
        } catch (e: IOException) {
            throw ApiError.Offline(e)
        }

        response.use { res ->
            if (!res.isSuccessful) {
                // Error responses are plain JSON even when the request asked to
                // stream, so reuse the same status mapping as every other call.
                val body = res.body?.string().orEmpty()
                val obj = runCatching { Api.json.parseToJsonElement(body) as? JsonObject }.getOrNull()
                val msg = obj?.get("details")?.stringOrNull()?.takeIf { it.isNotBlank() }
                    ?: obj?.get("error")?.stringOrNull()?.takeIf { it.isNotBlank() }
                throw when (res.code) {
                    401 -> ApiError.Unauthorized(msg)
                    403 -> ApiError.Forbidden(msg)
                    413 -> ApiError.TooLarge(msg)
                    429 -> ApiError.RateLimited(res.header("Retry-After")?.toIntOrNull(), msg)
                    else -> ApiError.Server(res.code, msg)
                }
            }

            val contentType = res.header("Content-Type").orEmpty()
            val body = res.body ?: throw ApiError.Malformed("no body")

            // The server honours a missing `stream`, so a non-SSE answer is a
            // valid shape, not an error. The web has the same passive fallback.
            if (!contentType.contains("text/event-stream")) {
                val obj = runCatching { Api.json.parseToJsonElement(body.string()) as? JsonObject }
                    .getOrNull() ?: throw ApiError.Malformed("non-JSON reply")
                return obj.toChatDone()
            }

            var done: ChatDone? = null
            val source = body.source()
            val frame = StringBuilder()

            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (line.isNotEmpty()) { frame.append(line).append('\n'); continue }

                // Blank line terminates a frame.
                consumeFrame(frame.toString(), onDelta) { done = it }
                frame.setLength(0)
                if (done != null) break
            }
            if (done == null && frame.isNotEmpty()) {
                consumeFrame(frame.toString(), onDelta) { done = it }
            }

            return done ?: throw ApiError.Server(200, "Reply interrupted. Please try again.")
        }
    }

    private inline fun consumeFrame(
        frame: String,
        onDelta: (String) -> Unit,
        onDone: (ChatDone) -> Unit,
    ) {
        for (line in frame.split("\n")) {
            if (!line.startsWith("data:")) continue
            val json = line.substring(5).trimStart()
            if (json.isEmpty()) continue
            val obj = runCatching { Api.json.parseToJsonElement(json) as? JsonObject }.getOrNull()
                ?: continue
            when (obj["type"]?.stringOrNull()) {
                "delta" -> onDelta(obj["text"]?.stringOrNull().orEmpty())
                "done" -> (obj["data"] as? JsonObject)?.let { onDone(it.toChatDone()) }
                // The server's only streamed error. Abandon the rest of the
                // stream; the caller keeps whatever text already arrived.
                "error" -> throw ApiError.Server(
                    200,
                    obj["message"]?.stringOrNull() ?: "Reply interrupted.",
                )
            }
        }
    }
}

internal fun JsonObject.toChatDone(): ChatDone {
    val blocked = this["blocked"]?.boolOrFalse() ?: false
    val reviewed = this["reviewed"]?.boolOrFalse() ?: false
    return ChatDone(
        response = this["response"]?.stringOrNull().orEmpty(),
        blocked = blocked,
        reviewed = reviewed,
        materialGap = this["material_gap"]?.boolOrFalse() ?: false,
        // Empty for students at the API level (app.py:1952-1953), not merely
        // hidden — so there is no student-facing sources UI to build.
        sources = (this["sources"] as? JsonArray)?.mapNotNull { it.sourceLabel() }
            ?: (this["rules_used"] as? JsonArray)?.mapNotNull { it.stringOrNull() }
            ?: emptyList(),
        toolkits = (this["toolkits"] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: emptyList(),
        toolRequest = (this["tool_request"] as? JsonObject)?.let { tr ->
            val type = tr["type"]?.stringOrNull() ?: return@let null
            ToolRequest(type, tr["topic"]?.stringOrNull().orEmpty())
        },
        chatId = this["chat_id"]?.stringOrNull(),
        title = this["title"]?.stringOrNull(),
    )
}

private fun kotlinx.serialization.json.JsonElement.boolOrFalse(): Boolean =
    runCatching { jsonPrimitive.boolean }.getOrElse { false }

/** Sources arrive either as plain excerpt strings or as {label, excerpt, section}. */
private fun kotlinx.serialization.json.JsonElement.sourceLabel(): String? {
    stringOrNull()?.let { return it }
    val o = runCatching { jsonObject }.getOrNull() ?: return null
    val label = o["label"]?.stringOrNull() ?: return null
    val section = o["section"]?.stringOrNull()?.let { " · section $it" }.orEmpty()
    val excerpt = o["excerpt"]?.stringOrNull()?.let { " — $it" }.orEmpty()
    return label + section + excerpt
}
