package com.chronos.tutor.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf

/** Reaches the tokens with no honest M3 slot. See [ChronosColors]. */
val LocalChronosColors = staticCompositionLocalOf { ChronosColorsLight }

/**
 * No dynamic colour. The palette is the brand — letting Android recolour it
 * from the wallpaper would erase the thing that makes the app recognisable.
 */
@Composable
fun ChronosTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) ChronosDark else ChronosLight
    val extras = if (darkTheme) ChronosColorsDark else ChronosColorsLight

    CompositionLocalProvider(LocalChronosColors provides extras) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography = ChronosTypography,
            shapes = ChronosShapes,
            content = content,
        )
    }
}
