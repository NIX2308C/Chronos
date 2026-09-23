package com.chronos.tutor

import com.chronos.tutor.data.filesPath
import com.chronos.tutor.net.Api
import com.chronos.tutor.net.toChatDone
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChatParseTest {

    private fun obj(s: String) = Api.json.parseToJsonElement(s) as JsonObject

    @Test
    fun `a reply without toolkits leaves them unknown`() {
        assertNull(obj("""{"response":"hi"}""").toChatDone().toolkits)
    }

    @Test
    fun `a reply with toolkits carries them, even when empty`() {
        assertEquals(listOf("quiz"), obj("""{"toolkits":["quiz"]}""").toChatDone().toolkits)
        assertEquals(emptyList<String>(), obj("""{"toolkits":[]}""").toChatDone().toolkits)
    }

    @Test
    fun `files query encodes both ids`() {
        assertEquals(
            "/student/files?class_id=a%26b&chat_id=c+d%3F",
            filesPath("a&b", "c d?"),
        )
    }
}
