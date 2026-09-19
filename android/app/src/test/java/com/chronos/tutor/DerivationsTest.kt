package com.chronos.tutor

import com.chronos.tutor.data.gapFrom
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The message annotation rule.
 *
 * app.py can set `reviewed` and `material_gap` at the same time
 * (app.py:1917-1919), so precedence is real logic, not a formality. The web
 * computes this in two separate places (student.html:557 and :1142) that must
 * agree; in Kotlin it exists once, and this pins it.
 */
class DerivationsTest {

    @Test
    fun `a plain material gap shows the gap note`() {
        assertTrue(gapFrom(materialGap = true, reviewed = false, blocked = false))
    }

    @Test
    fun `reviewed beats gap`() {
        // Both true is a real server state: the answer came from the student's
        // own attachment, which is not a hole in the course material.
        assertFalse(gapFrom(materialGap = true, reviewed = true, blocked = false))
    }

    @Test
    fun `blocked beats gap`() {
        assertFalse(gapFrom(materialGap = true, reviewed = false, blocked = true))
    }

    @Test
    fun `blocked and reviewed together still suppress the gap`() {
        assertFalse(gapFrom(materialGap = true, reviewed = true, blocked = true))
    }

    @Test
    fun `no gap flag means no gap note regardless of the others`() {
        assertFalse(gapFrom(materialGap = false, reviewed = false, blocked = false))
        assertFalse(gapFrom(materialGap = false, reviewed = true, blocked = false))
        assertFalse(gapFrom(materialGap = false, reviewed = false, blocked = true))
    }
}
