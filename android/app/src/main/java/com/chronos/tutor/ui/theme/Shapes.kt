package com.chronos.tutor.ui.theme

import androidx.compose.material3.Shapes
import androidx.compose.ui.graphics.RectangleShape

/**
 * The entire "no rounded corners anywhere" rule, in one place.
 *
 * web/student.html forces every Tailwind borderRadius to "0"; this is the
 * Compose equivalent, and it applies to every M3 component automatically.
 */
val ChronosShapes = Shapes(
    extraSmall = RectangleShape,
    small      = RectangleShape,
    medium     = RectangleShape,
    large      = RectangleShape,
    extraLarge = RectangleShape,
)
