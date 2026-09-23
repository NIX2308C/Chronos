package com.chronos.tutor

import com.chronos.tutor.ui.common.MdBlock
import com.chronos.tutor.ui.common.parseMarkdown
import com.chronos.tutor.ui.common.previewMarkdown
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The renderer turns model output into what the student actually reads, so a
 * bug here is visible on every single answer. These cases pin the web's
 * behaviour (web/student.html:456-489) including its deliberate limits — a
 * "nicer" markdown implementation that diverges from the web is a bug, because
 * the two clients must render the same answer the same way.
 */
class MarkdownTest {

    private fun text(b: MdBlock): String = when (b) {
        is MdBlock.Paragraph -> b.spans.joinToString("") { it.text }
        is MdBlock.Heading -> b.spans.joinToString("") { it.text }
        is MdBlock.Quote -> b.spans.joinToString("") { it.text }
        is MdBlock.Code -> b.text
        is MdBlock.Bullets -> b.items.joinToString("|") { i -> i.joinToString("") { it.text } }
        is MdBlock.Numbered -> b.items.joinToString("|") { i -> i.joinToString("") { it.text } }
    }

    @Test
    fun `plain paragraph`() {
        val b = parseMarkdown("Photosynthesis converts light into sugar.")
        assertEquals(1, b.size)
        assertTrue(b[0] is MdBlock.Paragraph)
        assertEquals("Photosynthesis converts light into sugar.", text(b[0]))
    }

    @Test
    fun `blank lines separate paragraphs, single newlines do not`() {
        val b = parseMarkdown("one\ntwo\n\nthree")
        assertEquals(2, b.size)
        assertEquals("one\ntwo", text(b[0]))
        assertEquals("three", text(b[1]))
    }

    @Test
    fun `headings clamp at level 3`() {
        val b = parseMarkdown("# a\n\n## b\n\n###### f")
        assertEquals(1, (b[0] as MdBlock.Heading).level)
        assertEquals(2, (b[1] as MdBlock.Heading).level)
        // h4-h6 all render as h3 on the web; matching that is the point.
        assertEquals(3, (b[2] as MdBlock.Heading).level)
    }

    @Test
    fun `bold and italic`() {
        val spans = (parseMarkdown("say **loud** and *soft*")[0] as MdBlock.Paragraph).spans
        assertTrue(spans.any { it.text == "loud" && it.bold })
        assertTrue(spans.any { it.text == "soft" && it.italic })
    }

    @Test
    fun `underscore forms also work`() {
        val spans = (parseMarkdown("__b__ and _i_")[0] as MdBlock.Paragraph).spans
        assertTrue(spans.any { it.text == "b" && it.bold })
        assertTrue(spans.any { it.text == "i" && it.italic })
    }

    @Test
    fun `bold wins over italic so double asterisks never split`() {
        val spans = (parseMarkdown("**x**")[0] as MdBlock.Paragraph).spans
        assertEquals(1, spans.size)
        assertEquals("x", spans[0].text)
        assertTrue(spans[0].bold)
        assertTrue(!spans[0].italic)
    }

    @Test
    fun `links keep their href and only http schemes match`() {
        val ok = (parseMarkdown("see [docs](https://example.com/a_b)")[0] as MdBlock.Paragraph).spans
        assertTrue(ok.any { it.text == "docs" && it.href == "https://example.com/a_b" })

        // javascript: is not a link on the web either — it stays literal text.
        val bad = (parseMarkdown("[x](javascript:alert(1))")[0] as MdBlock.Paragraph).spans
        assertTrue(bad.none { it.href != null })
    }

    @Test
    fun `an underscore inside a link url does not become italic`() {
        val spans = (parseMarkdown("[a](https://e.com/a_b_c)")[0] as MdBlock.Paragraph).spans
        assertEquals(1, spans.size)
        assertEquals("https://e.com/a_b_c", spans[0].href)
    }

    @Test
    fun `inline code is literal and beats other markup`() {
        val spans = (parseMarkdown("use `a * b` here")[0] as MdBlock.Paragraph).spans
        val code = spans.first { it.code }
        assertEquals("a * b", code.text)
        assertTrue(spans.none { it.italic })
    }

    @Test
    fun `fenced code keeps its content verbatim and drops the language`() {
        val b = parseMarkdown("before\n\n```kotlin\nval x = *y*\n```\n\nafter")
        val code = b.filterIsInstance<MdBlock.Code>().single()
        assertEquals("val x = *y*", code.text)
        assertEquals(3, b.size)
    }

    @Test
    fun `bullet lists, any marker`() {
        val b = parseMarkdown("- a\n* b\n+ c")
        assertEquals("a|b|c", text(b.single() as MdBlock.Bullets))
    }

    @Test
    fun `numbered lists drop the written number`() {
        val b = parseMarkdown("3. first\n7. second")
        val n = b.single() as MdBlock.Numbered
        assertEquals(2, n.items.size)
        assertEquals("first|second", text(n))
    }

    @Test
    fun `blockquote joins consecutive lines into one block`() {
        val b = parseMarkdown("> one\n> two")
        assertEquals("one\ntwo", text(b.single() as MdBlock.Quote))
    }

    @Test
    fun `carriage returns are normalised`() {
        val b = parseMarkdown("a\r\n\r\nb")
        assertEquals(2, b.size)
    }

    @Test
    fun `empty and blank input produce no blocks`() {
        assertTrue(parseMarkdown("").isEmpty())
        assertTrue(parseMarkdown("   \n\n  ").isEmpty())
    }

    @Test
    fun `a lone asterisk does not crash or swallow the line`() {
        val b = parseMarkdown("2 * 3 = 6")
        assertEquals("2 * 3 = 6", text(b.single()))
    }

    @Test
    fun `unterminated bold stays literal`() {
        val b = parseMarkdown("**not closed")
        assertEquals("**not closed", text(b.single()))
    }

    @Test
    fun `mixed document keeps block order`() {
        val md = """
            # Title

            Intro **bold**.

            - one
            - two

            > quote

            ```
            code
            ```
        """.trimIndent()
        val b = parseMarkdown(md)
        assertTrue(b[0] is MdBlock.Heading)
        assertTrue(b[1] is MdBlock.Paragraph)
        assertTrue(b[2] is MdBlock.Bullets)
        assertTrue(b[3] is MdBlock.Quote)
        assertTrue(b[4] is MdBlock.Code)
    }

    @Test
    fun `an unclosed fence previews as code while streaming`() {
        val b = previewMarkdown("Try this:\n```kotlin\nval x = 1")
        assertEquals(2, b.size)
        assertEquals("val x = 1", (b[1] as MdBlock.Code).text)
    }

    @Test
    fun `a closed fence previews the same as the final render`() {
        val src = "Hi\n```\ncode\n```"
        assertEquals(parseMarkdown(src), previewMarkdown(src))
    }
}
