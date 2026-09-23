package com.chronos.tutor.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.takeOrElse
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
fun MarkdownText(
    source: String,
    modifier: Modifier = Modifier,
    /** Still arriving: an unclosed ``` shows as code rather than literal backticks. */
    streaming: Boolean = false,
    color: Color = Color.Unspecified,
) {
    val blocks = remember(source, streaming) { if (streaming) previewMarkdown(source) else parseMarkdown(source) }
    val extras = LocalChronosColors.current
    val ink = color.takeOrElse { MaterialTheme.colorScheme.onSurface }

    Column(modifier) {
        blocks.forEachIndexed { i, block ->
            if (i > 0) Spacer(Modifier.height(10.dp))
            when (block) {
                is MdBlock.Paragraph -> Text(
                    text = block.spans.annotated(),
                    style = MaterialTheme.typography.titleMedium,
                    color = ink,
                )

                is MdBlock.Heading -> Text(
                    text = block.spans.annotated(),
                    // 1.25rem / 1.1rem / 1rem, as .ai-prose h1-h3 (student.html:430).
                    style = MaterialTheme.typography.labelLarge.copy(
                        fontSize = when (block.level) { 1 -> 20.sp; 2 -> 17.6.sp; else -> 16.sp },
                    ),
                    color = ink,
                    fontFamily = Schibsted,
                    fontWeight = FontWeight.SemiBold,
                )

                is MdBlock.Quote -> Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                    Spacer(
                        Modifier
                            .width(4.dp)
                            .fillMaxHeight()
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
                    block.items.forEach { item -> ListRow("▪", item, ink) }
                }

                is MdBlock.Numbered -> Column {
                    // The written number is dropped on the web too; lists always
                    // restart at 1.
                    block.items.forEachIndexed { n, item -> ListRow("${n + 1}.", item, ink) }
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
private fun ListRow(marker: String, spans: List<MdSpan>, ink: Color) {
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
            color = ink,
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
            if (s.href != null) {
                // Tappable: Text opens LinkAnnotation.Url in the browser itself.
                withLink(LinkAnnotation.Url(s.href, TextLinkStyles(style))) { append(s.text) }
            } else {
                pushStyle(style)
                append(s.text)
                pop()
            }
        }
    }
}
