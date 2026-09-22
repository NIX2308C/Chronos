package com.chronos.tutor.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.DialogProperties
import com.chronos.tutor.ui.theme.LocalChronosColors

data class TourStep(val icon: String, val title: String, val text: String)

/** Step copy is verbatim from each web page's tutorial. */
val STUDENT_TOUR = listOf(
    TourStep("school", "Pick your course", "The menu on the left sets which course the tutor answers from."),
    TourStep("forum", "Just ask", "Type a question and send it. Attach an assignment or rubric with + to get feedback on your own work."),
    TourStep("auto_awesome", "Practice", "When your teacher turns them on, quizzes and flashcards are in the sparkle menu."),
)
val MATERIAL_TOUR = listOf(
    TourStep("add", "Make a course", "Create one, then share its join code (in the menu) with your students."),
    TourStep("description", "Add material", "Upload PDFs, Word docs or notes. Chronos reads them and builds an outline of what the course covers."),
    TourStep("gavel", "Set the rules", "Base rules and your own rules shape every answer. Changes save as you make them."),
)
val STATS_TOUR = listOf(
    TourStep("school", "One course at a time", "Every number is for the course selected in the menu, over the range at the top."),
    TourStep("help", "Gaps first", "Questions your material couldn't answer, most-asked first: a ready-made list of what to upload next."),
    TourStep("forum", "Topics and repeats", "Questions are grouped by topic, and anything asked again and again is worth a minute of class time."),
)

/**
 * Shows [steps] once ([seen] == false) and whenever content calls its open lambda.
 * [seen] is null until the stored flag loads, so a returning user never sees it flash.
 */
@Composable
fun TourHost(
    seen: Boolean?,
    steps: List<TourStep>,
    onSeen: () -> Unit,
    content: @Composable (openTour: () -> Unit) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    LaunchedEffect(seen, steps) { if (seen == false) open = true }
    content { open = true }
    if (open) Tour(steps) { open = false; onSeen() }
}

@Composable
private fun Tour(steps: List<TourStep>, onDone: () -> Unit) {
    val extras = LocalChronosColors.current
    var i by remember(steps) { mutableIntStateOf(0) }
    val step = steps[i]
    AlertDialog(
        onDismissRequest = {},   // backdrop taps don't close it, as on the web
        properties = DialogProperties(dismissOnClickOutside = false),
        shape = RectangleShape,
        icon = { Sym(step.icon, size = 28.sp, tint = extras.crimsonFill) },
        title = { Text(step.title) },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                Text(step.text)
                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    steps.indices.forEach { d ->
                        Box(Modifier.size(7.dp).background(if (d == i) extras.crimsonFill else extras.rule))
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { if (i < steps.lastIndex) i++ else onDone() }) {
                Text(if (i < steps.lastIndex) "Next" else "Got it", color = extras.crimsonFill)
            }
        },
        dismissButton = {
            if (i > 0) TextButton(onClick = { i-- }) { Text("Back") }
            else TextButton(onClick = onDone) { Text("Skip", color = extras.muted) }
        },
    )
}
