package com.chronos.tutor.data

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ChatDone
import com.chronos.tutor.net.ChatStream
import com.chronos.tutor.net.sourceLabels
import com.chronos.tutor.net.stringOrNull
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlin.random.Random

class ChatRepository(private val api: Api, private val stream: ChatStream) {

    /**
     * Temp id for a chat the server has not seen yet. This exact string is sent
     * as `chat_id` and becomes the Firestore document id (app.py:1852), so the
     * server's `chat_id` in the reply comes back identical — adopting it is a
     * no-op for the id itself, and the title is the real payload.
     */
    fun tempId(): String = "new_" + Random.nextLong().toString(36).trimStart('-').take(8)

    suspend fun listChats(): List<Chat> = withContext(Dispatchers.IO) {
        val arr = api.get("/chats")["chats"] as? JsonArray ?: return@withContext emptyList()
        arr.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            Chat(
                id = o["id"]?.stringOrNull() ?: return@mapNotNull null,
                title = o["title"]?.stringOrNull().orEmpty().ifBlank { "New chat" },
                classId = o["class_id"]?.stringOrNull().orEmpty(),
            )
        }
    }

    suspend fun loadMessages(chatId: String): List<Message> = withContext(Dispatchers.IO) {
        val arr = api.get("/chats/$chatId/messages")["messages"] as? JsonArray
            ?: return@withContext emptyList()
        arr.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            // The wire role is 'student' or 'teacher'; 'teacher' is the tutor's
            // own voice, not a human teacher.
            val student = o["role"]?.stringOrNull() == "student"
            val blocked = o["blocked"]?.boolOrFalse() ?: false
            val reviewed = o["reviewed"]?.boolOrFalse() ?: false
            Message(
                role = if (student) Message.Role.STUDENT else Message.Role.TUTOR,
                content = o["content"]?.stringOrNull().orEmpty(),
                blocked = blocked,
                reviewed = reviewed,
                gap = !student && gapFrom(o["material_gap"]?.boolOrFalse() ?: false, reviewed, blocked),
                sources = o.sourceLabels("rules"),
                tool = parseTool(o["tool"] as? JsonObject),
            )
        }
    }

    /** Builds one activity. The server also saves it into the chat's history. */
    suspend fun runTool(classId: String, chatId: String, req: ToolRequest): LearningTool = withContext(Dispatchers.IO) {
        val o = api.post("/tools/run", buildJsonObject {
            put("class_id", classId)
            put("chat_id", chatId)
            put("tool_request", buildJsonObject { put("type", req.type); put("topic", req.topic) })
        }, slow = true)
        parseTool(o["tool"] as? JsonObject) ?: throw com.chronos.tutor.net.ApiError.Server(200, "Couldn't create that activity.")
    }

    suspend fun deleteChat(chatId: String) = withContext(Dispatchers.IO) {
        api.delete("/chats/$chatId"); Unit
    }

    /**
     * Sends a message and streams the reply. Runs on IO; [onDelta] fires on that
     * same thread, so callers marshal to the UI themselves.
     */
    suspend fun send(
        message: String,
        classId: String,
        chatId: String,
        onDelta: (String) -> Unit,
    ): ChatDone = withContext(Dispatchers.IO) {
        stream.send(message, classId, chatId, onDelta)
    }

    suspend fun health(): Boolean = withContext(Dispatchers.IO) {
        runCatching { api.get("/health") }.isSuccess
    }
}

class ClassRepository(private val api: Api) {

    suspend fun list(): List<CourseClass> = withContext(Dispatchers.IO) {
        val arr = api.get("/classes")["classes"] as? JsonArray ?: return@withContext emptyList()
        arr.mapNotNull { el ->
            val o = el as? JsonObject ?: return@mapNotNull null
            CourseClass(
                id = o["id"]?.stringOrNull() ?: return@mapNotNull null,
                name = o["name"]?.stringOrNull().orEmpty().ifBlank { "Course" },
                toolkits = (o["toolkits"] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: emptyList(),
                joinCode = o["join_code"]?.stringOrNull(),
            )
        }
    }

    /** Join codes are 6 chars from an unambiguous alphabet; the server upper-cases. */
    suspend fun join(code: String): CourseClass = withContext(Dispatchers.IO) {
        val o = api.post("/classes/join", buildJsonObject {
            put("join_code", JsonPrimitive(code.trim().uppercase()))
        })
        CourseClass(
            id = o["id"]?.stringOrNull().orEmpty(),
            name = o["name"]?.stringOrNull().orEmpty(),
            toolkits = (o["toolkits"] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: emptyList(),
        )
    }
}

private fun kotlinx.serialization.json.JsonElement.boolOrFalse(): Boolean =
    runCatching { jsonPrimitive.boolean }.getOrElse { false }
