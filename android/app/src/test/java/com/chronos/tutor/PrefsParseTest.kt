package com.chronos.tutor

import com.chronos.tutor.data.TutorPrefs
import com.chronos.tutor.data.parsePrefs
import com.chronos.tutor.net.Api
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** GET/POST /me/preferences shape (app.py: PREFERENCE_DEFAULTS, PERSONALITIES). */
class PrefsParseTest {

    @Test
    fun `preferences and personalities parse, missing keys default`() {
        val (p, list) = parsePrefs(Api.json.parseToJsonElement("""{
            "preferences":{"personality":"socratic","explain_simply":true,"language":null},
            "personalities":[{"id":"default","label":"Default"},{"id":"socratic","label":"Socratic"}]}""") as JsonObject)
        assertEquals("socratic", p.personality)
        assertTrue(p.explainSimply)
        assertEquals("", p.language)
        assertEquals(TutorPrefs().length, p.length)
        assertEquals(listOf("default", "socratic"), list.map { it.id })
    }
}
