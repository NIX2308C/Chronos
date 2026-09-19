package com.chronos.tutor.ui.common

import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import com.chronos.tutor.ui.theme.MaterialSymbolsSharp

/**
 * A Material Symbols Sharp glyph, rendered by ligature exactly as the web pages
 * do (`<span class="msym">chat_bubble</span>`).
 *
 * Compose's bundled icon set has no Sharp cut, and material-icons-extended is
 * ~8MB of the wrong one. Shipping the font instead means zero icon code and the
 * same glyphs as the web app, named identically — so a name copied out of the
 * HTML just works.
 */
@Composable
fun Sym(
    name: String,
    modifier: Modifier = Modifier,
    size: TextUnit = 24.sp,
    tint: Color = LocalContentColor.current,
) {
    Text(
        text = name,
        modifier = modifier,
        style = TextStyle(
            fontFamily = MaterialSymbolsSharp,
            fontSize = size,
            color = tint,
        ),
    )
}
