package com.chronos.tutor.ui.theme

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color

/**
 * The palette, transcribed from the web app so the two clients cannot drift.
 *
 * Light values come from the tailwind.config block at the top of
 * web/student.html; dark values from the `html.dark` block in web/theme.css.
 * Those token names are Material-Design-shaped already, which is why this maps
 * onto an M3 ColorScheme almost literally.
 *
 * Rule carried over from the web: when the palette changes, change the VALUE,
 * never the token name — the web page builds class strings from those names at
 * runtime.
 */

// Brand constants, identical in both themes unless noted.
val Crimson     = Color(0xFFE2001A)   // THE accent — brand, not "danger"
val Gold        = Color(0xFFC9A227)   // "verified/cited" seal, distinct from crimson
val Ink         = Color(0xFF141311)
val Paper       = Color(0xFFF7F4EC)
val Steel       = Color(0xFF4B4A46)

// Light
private val PageLight     = Color(0xFFE9E4D6)
private val PaperLight     = Color(0xFFF7F4EC)
private val RaisedLight    = Color(0xFFE4DECD)
private val RuleLight      = Color(0xFFDED8C6)
private val EdgeLight      = Color(0xFF1C1A16)
private val SubLight       = Color(0xFF5D5B55)
private val OutlineLight   = Color(0xFFB9B09A)
private val ErrContLight   = Color(0xFFF6D9D5)
private val OnErrContLight = Color(0xFF8E0011)
private val SecContLight   = Color(0xFFF2E7C4)

// Dark
private val PageDark    = Color(0xFF0D0D10)
private val PaperDark   = Color(0xFF16161B)
private val RaisedDark  = Color(0xFF212128)
private val TextDark    = Color(0xFFF7F4EC)
private val MutedDark   = Color(0xFFB3AEA3)
private val EdgeDark    = Color(0xFF3D3D46)

/**
 * Crimson lightened for TEXT on near-black. Fills keep the true brand red —
 * that distinction is load-bearing in theme.css and is preserved here as two
 * separate values rather than one that compromises between them.
 */
private val CrimsonTextDark = Color(0xFFFF3B4E)

val ChronosLight = lightColorScheme(
    primary            = Crimson,
    onPrimary          = Paper,
    primaryContainer   = ErrContLight,
    onPrimaryContainer = OnErrContLight,
    secondary          = Gold,
    onSecondary        = Color(0xFF0B0B0C),
    secondaryContainer = SecContLight,
    onSecondaryContainer = Color(0xFF6B5410),
    tertiary           = Crimson,
    onTertiary         = Paper,
    background         = PageLight,
    onBackground       = Ink,
    surface            = PageLight,
    onSurface          = Ink,
    surfaceVariant     = RaisedLight,
    onSurfaceVariant   = Steel,
    surfaceContainerLowest = PaperLight,
    surfaceContainerLow    = PaperLight,
    surfaceContainer       = Color(0xFFEFEADC),
    surfaceContainerHigh   = RaisedLight,
    surfaceContainerHighest= RuleLight,
    outline            = OutlineLight,
    outlineVariant     = EdgeLight,
    error              = Crimson,
    onError            = Paper,
    errorContainer     = ErrContLight,
    onErrorContainer   = OnErrContLight,
)

val ChronosDark = darkColorScheme(
    primary            = CrimsonTextDark,
    onPrimary          = Paper,
    primaryContainer   = Color(0xFF321014),
    onPrimaryContainer = Color(0xFFFF9A91),
    secondary          = Gold,
    onSecondary        = Color(0xFF0B0B0C),
    secondaryContainer = Color(0xFF2A2413),
    onSecondaryContainer = Color(0xFFE8D79B),
    tertiary           = CrimsonTextDark,
    onTertiary         = Paper,
    background         = PageDark,
    onBackground       = TextDark,
    surface            = PageDark,
    onSurface          = TextDark,
    surfaceVariant     = RaisedDark,
    onSurfaceVariant   = MutedDark,
    surfaceContainerLowest = PaperDark,
    surfaceContainerLow    = PaperDark,
    surfaceContainer       = PaperDark,
    surfaceContainerHigh   = RaisedDark,
    surfaceContainerHighest= RaisedDark,
    outline            = EdgeDark,
    outlineVariant     = EdgeDark,
    error              = CrimsonTextDark,
    onError            = Paper,
    errorContainer     = Color(0xFF321014),
    onErrorContainer   = Color(0xFFFF9A91),
)

/**
 * Tokens with no honest M3 slot. Reached through [LocalChronosColors] so a
 * composable never has to ask which theme is active.
 */
data class ChronosColors(
    val crimsonFill: Color,   // always the true brand red, both themes
    val gold: Color,
    val raised: Color,        // hover fills, chips, table stripes (--c-hi / --d-raised)
    val rule: Color,          // hairline borders (--c-rule / --d-edge)
    val muted: Color,         // secondary text (--c-sub / --d-muted)
    val correct: Color,       // right answers and finished activities (#1E7A4E / #6FD5A0)
)

val ChronosColorsLight = ChronosColors(
    crimsonFill = Crimson, gold = Gold, raised = RaisedLight, rule = RuleLight, muted = SubLight,
    correct = Color(0xFF1E7A4E),
)

val ChronosColorsDark = ChronosColors(
    crimsonFill = Crimson, gold = Gold, raised = RaisedDark, rule = EdgeDark, muted = MutedDark,
    correct = Color(0xFF6FD5A0),
)
