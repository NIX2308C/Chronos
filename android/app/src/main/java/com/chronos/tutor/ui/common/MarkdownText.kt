package com.chronos.tutor.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.Schibsted

/**
 * Renders the blocks from [parseMarkdown].
 *
 * Styling follows the .ai-prose rules in web/student.html:258-274 — square
 * bullets with a crimson marker, a crimson rule down the left of quotes, and
 * headings in the sans face rather than the serif the body uses.
 */
@Composable
fun MarkdownText(source: String, modifier: Modifier = Modifier) {
    val blocks = remember(source) { parseMarkdown(source) }
    val extras = LocalChronosColors.current

    Column(modifier) {
        blocks.forEachIndexed { i, block ->
            if (i > 0) Spacer(Modifier.height(10.dp))
            when (block) {
                is MdBlock.Paragraph -> Text(
                    text = block.spans.annotated(),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )

                is MdBlock.Heading -> Text(
                    text = block.spans.annotated(),
                    style = when (block.level) {
                        1 -> MaterialTheme.typography.labelLarge
                        2 -> MaterialTheme.typography.labelLarge
                        else -> MaterialTheme.typography.labelLarge
                    },
                    color = MaterialTheme.colorScheme.onSurface,
                    fontFamily = Schibsted,
                    fontWeight = FontWeight.SemiBold,
                )

                is MdBlock.Quote -> Row(Modifier.fillMaxWidth()) {
                    Spacer(
                        Modifier
                            .width(4.dp)
                            .height(20.dp)
                            .background(extras.crimsonFill)
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = block.spans.annotated(),
                        style = MaterialTheme.typography.titleMedium,
                        color = extras.muted,
                    )
                }

                is MdBlock.Bullets -> Column {
                    block.items.forEach { item -> ListRow("▪", item) }
                }

                is MdBlock.Numbered -> Column {
                    // The written number is dropped on the web too; lists always
                    // restart at 1.
                    block.items.forEachIndexed { n, item -> ListRow("${n + 1}.", item) }
                }

                is MdBlock.Code -> Text(
                    text = block.text,
                    style = MaterialTheme.typography.bodyMedium,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(extras.raised)
                        .horizontalScroll(rememberScrollState())
                        .padding(10.dp),
                )
            }
        }
    }
}

@Composable
private fun ListRow(marker: String, spans: List<MdSpan>) {
    val extras = LocalChronosColors.current
    Row(Modifier.padding(bottom = 4.dp)) {
        Text(
            text = marker,
            style = MaterialTheme.typography.titleMedium,
            color = extras.crimsonFill,
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = spans.annotated(),
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
private fun List<MdSpan>.annotated(): AnnotatedString {
    val extras = LocalChronosColors.current
    return buildAnnotatedString {
        this@annotated.forEach { s ->
            val style = SpanStyle(
                fontWeight = if (s.bold) FontWeight.SemiBold else null,
                fontStyle = if (s.italic) FontStyle.Italic else null,
                fontFamily = if (s.code) FontFamily.Monospace else null,
                background = if (s.code) extras.raised else androidx.compose.ui.graphics.Color.Unspecified,
                color = if (s.href != null) extras.crimsonFill else androidx.compose.ui.graphics.Color.Unspecified,
                textDecoration = if (s.href != null) TextDecoration.Underline else null,
            )
            pushStyle(style)
            append(s.text)
            pop()
        }
    }
}
