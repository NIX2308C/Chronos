package com.chronos.tutor.ui.theme

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * The overshoot easing the whole design system moves on: elements slam into
 * place rather than fade and drift. Straight from `--snap` in web/student.html.
 */
val Snap = CubicBezierEasing(0.2f, 1.45f, 0.35f, 1f)

/**
 * The hard offset shadow — a solid colour block behind the element, with no
 * blur and no elevation. This is the design's loudest signature (`box-shadow:
 * 7px 7px 0 var(--crimson)` on the web) and deliberately not M3 elevation,
 * which would render a soft grey drop shadow instead.
 */
fun Modifier.hardShadow(color: Color, offset: Dp = 7.dp): Modifier = drawBehind {
    val dx = offset.toPx()
    drawRect(
        color = color,
        topLeft = Offset(dx, dx),
        size = Size(size.width, size.height),
    )
}
