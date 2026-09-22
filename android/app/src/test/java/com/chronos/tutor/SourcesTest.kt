package com.chronos.tutor

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.sourceLabels
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

/** Teacher-only sources: the live `done` frame and reopened history must agree. */
class SourcesTest {

    private fun obj(s: String) = Api.json.parseToJsonElement(s) as JsonObject

    @Test
    fun `history sources as objects are kept`() {
        val o = obj("""{"sources":[{"label":"Ch 2.pdf","section":"3","excerpt":"x"}],"rules":["r1"]}""")
        assertEquals(listOf("Ch 2.pdf · section 3 — x"), o.sourceLabels("rules"))
    }

    @Test
    fun `plain string sources still work`() {
        assertEquals(listOf("a"), obj("""{"sources":["a"]}""").sourceLabels("rules_used"))
    }

    @Test
    fun `empty sources fall back to rules`() {
        assertEquals(listOf("r1"), obj("""{"sources":[],"rules_used":["r1"]}""").sourceLabels("rules_used"))
    }

    @Test
    fun `nothing gives an empty list`() {
        assertEquals(emptyList<String>(), obj("{}").sourceLabels("rules"))
    }
}
