package com.chronos.tutor.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density

/** Reaches the tokens with no honest M3 slot. See [ChronosColors]. */
val LocalChronosColors = staticCompositionLocalOf { ChronosColorsLight }

/** Settings → Reduce motion: count-ups, flips and bars jump straight to their end state. */
val LocalReduceMotion = staticCompositionLocalOf { false }

/**
 * No dynamic colour. The palette is the brand — letting Android recolour it
 * from the wallpaper would erase the thing that makes the app recognisable.
 *
 * [theme] is light | dark | system; [largeText] scales every sp like the web's text-size setting.
 */
@Composable
fun ChronosTheme(
    theme: String = "system",
    largeText: Boolean = false,
    reduceMotion: Boolean = false,
    content: @Composable () -> Unit,
) {
    val darkTheme = when (theme) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    val colorScheme = if (darkTheme) ChronosDark else ChronosLight
    val extras = if (darkTheme) ChronosColorsDark else ChronosColorsLight
    val density = LocalDensity.current

    CompositionLocalProvider(
        LocalChronosColors provides extras,
        LocalReduceMotion provides reduceMotion,
        LocalDensity provides if (largeText) Density(density.density, density.fontScale * 1.15f) else density,
    ) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography = ChronosTypography,
            shapes = ChronosShapes,
            content = content,
        )
    }
}
