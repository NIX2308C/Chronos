package com.chronos.tutor

import com.chronos.tutor.data.CourseSettings
import com.chronos.tutor.data.parseMaterial
import com.chronos.tutor.data.parseSettings
import com.chronos.tutor.data.parseStats
import com.chronos.tutor.data.settingsJson
import com.chronos.tutor.net.Api
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/** Response shapes of the teacher routes in app.py (/rules, /course-settings, /stats). */
class TeacherParseTest {

    private fun obj(s: String) = Api.json.parseToJsonElement(s) as JsonObject

    @Test
    fun `rules split into custom rules and grouped documents with manifest`() {
        val m = parseMaterial(obj("""{
            "rules":[
              {"id":"custom_a","text":"Hints first","source":null},
              {"id":"file_200_0","source":"b.pdf"},
              {"id":"file_100_0","source":"a.pdf"},
              {"id":"file_100_1","source":"a.pdf"}
            ],
            "manifest":{"file_100":{"summary":"Cells","topics":["osmosis"],"added":1}},
            "docs_truncated":true}"""))
        assertEquals(listOf("Hints first"), m.rules.map { it.text })
        assertEquals(listOf("b.pdf", "a.pdf"), m.docs.map { it.name })   // newest first
        val a = m.docs[1]
        assertEquals("file_100", a.base)
        assertEquals(2, a.chunks)
        assertEquals(100L, a.addedMs)
        assertEquals("Cells", a.summary)
        assertEquals(listOf("osmosis"), a.topics)
        assertNull(m.docs[0].summary)
        assertEquals(true, m.docsTruncated)
    }

    @Test
    fun `settings round-trip and missing keys take defaults`() {
        val s = parseSettings(obj("""{"settings":{"grounded_only":false,"hint_strength":"strong"}}"""))
        assertFalse(s.groundedOnly)
        assertEquals("strong", s.hintStrength)
        assertEquals(CourseSettings().practiceTools, s.practiceTools)
        assertEquals(s, parseSettings(buildJsonObject { put("settings", settingsJson(s)) }))
    }

    @Test
    fun `stats parse`() {
        val s = parseStats(obj("""{"total_questions":5,"students_active":2,"students_total":9,
            "grounded_conversations":4,"unanswered_count":1,
            "categories":[{"name":"Cells","count":3}],
            "unanswered":[{"question":"What is ATP?","count":2,"students":1}]}"""))
        assertEquals(5, s.totalQuestions)
        assertEquals(listOf("Cells" to 3), s.categories)
        assertEquals(2, s.unanswered.single().count)
        assertEquals(emptyList<Any>(), s.learningGaps)
    }
}
