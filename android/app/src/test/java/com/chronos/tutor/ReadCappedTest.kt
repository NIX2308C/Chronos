package com.chronos.tutor

import com.chronos.tutor.ui.common.FileTooLarge
import com.chronos.tutor.ui.common.readCapped
import org.junit.Assert.assertArrayEquals
import org.junit.Test
import java.io.ByteArrayInputStream

/** Picked files are read with a cap, so a huge pick cannot exhaust memory. */
class ReadCappedTest {

    @Test
    fun `reads a file at the limit`() {
        val bytes = ByteArray(100) { it.toByte() }
        assertArrayEquals(bytes, readCapped(ByteArrayInputStream(bytes), 100))
    }

    @Test(expected = FileTooLarge::class)
    fun `stops one byte past the limit`() {
        readCapped(ByteArrayInputStream(ByteArray(101)), 100)
    }

    @Test
    fun `empty file is fine`() {
        assertArrayEquals(ByteArray(0), readCapped(ByteArrayInputStream(ByteArray(0)), 100))
    }
}
