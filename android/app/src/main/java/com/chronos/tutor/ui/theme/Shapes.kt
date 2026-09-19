package com.chronos.tutor.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * The entire "no rounded corners anywhere" rule, in one place.
 *
 * web/student.html forces every Tailwind borderRadius to "0"; this is the
 * Compose equivalent, and it applies to every M3 component automatically.
 *
 * RoundedCornerShape(0.dp) rather than RectangleShape: M3's Shapes only accepts
 * a CornerBasedShape, which RectangleShape is not. They draw identically.
 */
private val Square = RoundedCornerShape(0.dp)

val ChronosShapes = Shapes(
    extraSmall = Square,
    small = Square,
    medium = Square,
    large = Square,
    extraLarge = Square,
)
