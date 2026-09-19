package com.chronos.tutor.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.chronos.tutor.R

/**
 * Type, transcribed from the fontFamily/fontSize blocks in web/student.html.
 *
 * Newsreader is the display serif, Schibsted Grotesk the UI sans — both bundled
 * in res/font rather than fetched through Downloadable Fonts, which would need
 * Play Services and a network round-trip before first paint.
 */

val Newsreader = FontFamily(
    Font(R.font.newsreader_regular,  FontWeight.Normal),
    Font(R.font.newsreader_medium,   FontWeight.Medium),
    Font(R.font.newsreader_semibold, FontWeight.SemiBold),
)

val Schibsted = FontFamily(
    Font(R.font.schibsted_regular,  FontWeight.Normal),
    Font(R.font.schibsted_medium,   FontWeight.Medium),
    Font(R.font.schibsted_semibold, FontWeight.SemiBold),
    Font(R.font.schibsted_bold,     FontWeight.Bold),
)

/** Material Symbols Sharp, rendered by ligature. See ui/common/Sym.kt. */
val MaterialSymbolsSharp = FontFamily(Font(R.font.material_symbols_sharp))

val ChronosTypography = Typography(
    // headline-xl — 46/48, +0.005em
    displayLarge = TextStyle(
        fontFamily = Newsreader, fontWeight = FontWeight.Normal,
        fontSize = 46.sp, lineHeight = 48.sp, letterSpacing = 0.005.em,
    ),
    // headline-lg — 32/34
    headlineLarge = TextStyle(
        fontFamily = Newsreader, fontWeight = FontWeight.Normal,
        fontSize = 32.sp, lineHeight = 34.sp,
    ),
    // headline-md / headline-lg-mobile — 24/26
    headlineMedium = TextStyle(
        fontFamily = Newsreader, fontWeight = FontWeight.Normal,
        fontSize = 24.sp, lineHeight = 26.sp,
    ),
    // The AI answer voice: .bub-ai is Newsreader 19px/1.62 in student.html.
    titleMedium = TextStyle(
        fontFamily = Newsreader, fontWeight = FontWeight.Normal,
        fontSize = 19.sp, lineHeight = 31.sp,
    ),
    // body-lg — 18/28
    bodyLarge = TextStyle(
        fontFamily = Schibsted, fontWeight = FontWeight.Normal,
        fontSize = 18.sp, lineHeight = 28.sp,
    ),
    // body-md — 15/24
    bodyMedium = TextStyle(
        fontFamily = Schibsted, fontWeight = FontWeight.Normal,
        fontSize = 15.sp, lineHeight = 24.sp,
    ),
    // label-md — 14/20, 600, +0.01em
    labelLarge = TextStyle(
        fontFamily = Schibsted, fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp, lineHeight = 20.sp, letterSpacing = 0.01.em,
    ),
    // label-sm — 11/16, 600, +0.12em. The wide tracking is the mono-ish
    // utility voice used for citations, stamps and nav labels.
    labelSmall = TextStyle(
        fontFamily = Schibsted, fontWeight = FontWeight.SemiBold,
        fontSize = 11.sp, lineHeight = 16.sp, letterSpacing = 0.12.em,
    ),
)
