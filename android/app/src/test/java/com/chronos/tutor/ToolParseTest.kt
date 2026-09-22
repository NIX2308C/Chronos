package com.chronos.tutor

import com.chronos.tutor.data.LearningTool
import com.chronos.tutor.data.parseTool
import com.chronos.tutor.net.Api
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The four shapes _parse_tool_result (app.py) returns from /tools/run and saves into history. */
class ToolParseTest {

    private fun tool(s: String) = parseTool(Api.json.parseToJsonElement(s) as JsonObject)

    @Test
    fun `quiz keeps valid questions and reveal flag`() {
        val q = tool("""{"type":"quiz","title":"Cells","reveal_final_answers":true,"questions":[
            {"prompt":"P1","options":["a","b","c","d"],"answer":2,"explanation":"e"},
            {"prompt":"bad","options":["a","b"],"answer":5}]}""") as LearningTool.Quiz
        assertEquals(1, q.questions.size)
        assertEquals(2, q.questions[0].answer)
        assertTrue(q.revealAnswers)
    }

    @Test
    fun `flashcards, concept map with an out-of-range link, review sheet`() {
        val f = tool("""{"type":"flashcards","title":"F","cards":[{"front":"x","back":"y"},{"front":"","back":"z"}]}""")
        assertEquals(listOf("x" to "y"), (f as LearningTool.Flashcards).cards)

        val m = tool("""{"type":"concept_map","nodes":[{"label":"A"},{"label":"B","detail":"d"}],
            "links":[{"from":0,"to":1,"label":"causes"},{"from":0,"to":9}]}""") as LearningTool.ConceptMap
        assertEquals("Concept map", m.title)
        assertEquals(1, m.links.size)

        val r = tool("""{"type":"review_sheet","title":"R","sections":[{"heading":"H","points":["p1","p2"]}]}""")
        assertEquals(listOf("H" to listOf("p1", "p2")), (r as LearningTool.ReviewSheet).sections)
    }

    @Test
    fun `unknown or empty tools are null`() {
        assertNull(tool("""{"type":"poem"}"""))
        assertNull(tool("""{"type":"quiz","questions":[]}"""))
        assertNull(parseTool(null))
    }
}
