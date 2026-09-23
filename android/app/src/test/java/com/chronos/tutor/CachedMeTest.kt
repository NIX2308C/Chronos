package com.chronos.tutor

import com.chronos.tutor.data.Me
import com.chronos.tutor.data.decodeMe
import com.chronos.tutor.data.encodeMe
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The last-known identity that lets launch skip the /auth/me wait. */
class CachedMeTest {

    @Test
    fun `round trips every field`() {
        val me = Me(uid = "u1", email = "a@b.c", role = "teacher", isDev = true)
        assertEquals(me, decodeMe(encodeMe(me)))
    }

    @Test
    fun `missing email stays null`() {
        val me = Me(uid = "u1", email = null, role = "student")
        assertEquals(me, decodeMe(encodeMe(me)))
    }

    @Test
    fun `anything unreadable means ask the server`() {
        assertNull(decodeMe(null))
        assertNull(decodeMe(""))
        assertNull(decodeMe("not json"))
        assertNull(decodeMe("""{"uid":"u1"}"""))          // no role
        assertNull(decodeMe("""{"role":"student"}"""))    // no uid
    }
}
