package com.chronos.tutor.ui.student

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateIntAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.LearningTool
import com.chronos.tutor.data.ToolStatus
import com.chronos.tutor.ui.common.Sounds
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.LocalReduceMotion
import kotlinx.coroutines.delay

private const val NONE = -1

@Composable
fun ToolStatusLine(status: ToolStatus, onStop: () -> Unit) {
    val extras = LocalChronosColors.current
    val color = when (status.state) {
        ToolStatus.State.FAILED -> MaterialTheme.colorScheme.error
        ToolStatus.State.USING -> extras.crimsonFill
        else -> extras.muted
    }
    Row(
        Modifier.fillMaxWidth().border(1.dp, extras.rule).padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (status.state == ToolStatus.State.USING) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp, color = color)
            Spacer(Modifier.width(10.dp))
        }
        Text(status.text, color = color, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        if (status.state == ToolStatus.State.USING) TextButton(onClick = onStop) { Text("Stop", color = extras.muted) }
    }
}

@Composable
fun LearningToolCard(tool: LearningTool, onSound: (Sounds.Kind) -> Unit, onReview: (String) -> Unit) {
    val extras = LocalChronosColors.current
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, extras.rule).padding(16.dp)
    ) {
        when (tool) {
            is LearningTool.Quiz -> Quiz(tool, onSound, onReview)
            is LearningTool.Flashcards -> Flashcards(tool)
            is LearningTool.ConceptMap -> ConceptMap(tool)
            is LearningTool.ReviewSheet -> ReviewSheet(tool)
        }
    }
}

@Composable
private fun Header(kind: String, title: String, badge: String? = null) {
    val extras = LocalChronosColors.current
    Row(verticalAlignment = Alignment.Top) {
        Column(Modifier.weight(1f)) {
            Text(kind.uppercase(), style = MaterialTheme.typography.labelSmall, color = extras.muted)
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
        badge?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = extras.crimsonFill) }
    }
    Spacer(Modifier.height(10.dp))
}

@Composable
private fun Bar(ratio: Float) {
    val extras = LocalChronosColors.current
    val p by animateFloatAsState(ratio.coerceIn(0f, 1f), if (LocalReduceMotion.current) snap() else tween(300), label = "bar")
    Box(Modifier.fillMaxWidth().height(4.dp).background(extras.raised)) {
        Box(Modifier.fillMaxWidth(p).fillMaxHeight().background(extras.crimsonFill))
    }
    Spacer(Modifier.height(14.dp))
}

@Composable
private fun CountUp(value: Int, total: Int) {
    var target by remember { mutableIntStateOf(0) }
    LaunchedEffect(value) { target = value }
    val shown by animateIntAsState(target, if (LocalReduceMotion.current) snap() else tween(800), label = "score")
    Text("$shown / $total", fontSize = 34.sp, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun ToolButton(text: String, primary: Boolean = false, enabled: Boolean = true, onClick: () -> Unit) {
    val extras = LocalChronosColors.current
    if (primary) Button(
        onClick = onClick, enabled = enabled, shape = RectangleShape,
        colors = ButtonDefaults.buttonColors(containerColor = extras.crimsonFill, contentColor = MaterialTheme.colorScheme.onPrimary),
    ) { Text(text) }
    else OutlinedButton(onClick = onClick, enabled = enabled, shape = RectangleShape) { Text(text) }
}

// ---- quiz -------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Quiz(tool: LearningTool.Quiz, onSound: (Sounds.Kind) -> Unit, onReview: (String) -> Unit) {
    val extras = LocalChronosColors.current
    val qs = tool.questions
    var order by rememberSaveable { mutableStateOf(IntArray(qs.size) { it }) }
    var answers by rememberSaveable { mutableStateOf(IntArray(qs.size) { NONE }) }
    var index by rememberSaveable { mutableIntStateOf(0) }
    var finished by rememberSaveable { mutableStateOf(false) }
    var reviewSent by rememberSaveable { mutableStateOf(false) }
    var readingUntil by remember { mutableLongStateOf(0L) }

    fun goNext() { if (index < order.size - 1) index++ else { finished = true } }

    if (finished) {
        val score = qs.indices.count { answers[it] == qs[it].answer }
        LaunchedEffect(Unit) { onSound(if (score.toFloat() / qs.size >= .7f) Sounds.Kind.HIGH_SCORE else Sounds.Kind.LOW_SCORE) }
        val missed = qs.indices.filter { answers[it] != qs[it].answer }
        Header("Practice quiz", tool.title, "Complete")
        Bar(1f)
        Text("QUIZ COMPLETE", style = MaterialTheme.typography.labelSmall, color = extras.muted)
        CountUp(score, qs.size)
        Text(
            if (score == qs.size) "Strong recall. Keep the ideas connected by explaining one in your own words."
            else "Your result is ready for a focused follow-up in this chat.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(10.dp))
        qs.forEachIndexed { i, q ->
            val ok = answers[i] == q.answer
            Row(Modifier.padding(vertical = 3.dp)) {
                Text(if (ok) "✓" else "✗", color = if (ok) extras.gold else MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                Text(q.prompt + if (!ok && tool.revealAnswers) " — " + q.options[q.answer] else "",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        Spacer(Modifier.height(12.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ToolButton(if (reviewSent) "Sent to AI" else "Review with AI", primary = true, enabled = !reviewSent) {
                reviewSent = true
                onReview(quizSummary(tool, answers, score))
            }
            if (missed.isNotEmpty()) ToolButton("Retry missed (${missed.size})") {
                order = missed.toIntArray()
                answers = answers.copyOf().also { a -> missed.forEach { a[it] = NONE } }
                index = 0; finished = false
            }
            ToolButton("Try again") {
                order = IntArray(qs.size) { it }; answers = IntArray(qs.size) { NONE }; index = 0; finished = false
            }
        }
        return
    }

    val qn = order[index]
    val q = qs[qn]
    val answer = answers[qn]

    // Auto-advance 5s after answering, held while the student is still touching the card.
    LaunchedEffect(qn, answer) {
        if (answer == NONE) return@LaunchedEffect
        delay(5_000)
        while (System.currentTimeMillis() < readingUntil) delay(900)
        goNext()
    }

    Header("Practice quiz", tool.title, "Question ${index + 1} of ${order.size}")
    Bar(order.count { answers[it] != NONE }.toFloat() / order.size)
    Column(Modifier.pointerInput(Unit) {
        awaitPointerEventScope {
            while (true) { awaitPointerEvent(PointerEventPass.Initial); readingUntil = System.currentTimeMillis() + 5_000 }
        }
    }) {
        Text(q.prompt, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
        Spacer(Modifier.height(10.dp))
        q.options.forEachIndexed { oi, option ->
            val chosen = answer == oi
            val showCorrect = answer != NONE && oi == q.answer && (chosen || tool.revealAnswers)
            val border = when {
                showCorrect -> extras.gold
                chosen -> MaterialTheme.colorScheme.error
                else -> extras.rule
            }
            Row(
                Modifier.fillMaxWidth().padding(bottom = 8.dp).border(1.dp, border)
                    .clickable(enabled = answer == NONE) {
                        answers = answers.copyOf().also { it[qn] = oi }
                        onSound(if (oi == q.answer) Sounds.Kind.CORRECT else Sounds.Kind.INCORRECT)
                    }
                    .padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(('A' + oi).toString(), fontWeight = FontWeight.Bold, color = extras.crimsonFill)
                Spacer(Modifier.width(12.dp))
                Text(option, style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (answer != NONE) {
            val right = answer == q.answer
            Text(
                when {
                    right -> "Correct. " + q.explanation.ifBlank { "Nice work—keep going." }
                    tool.revealAnswers -> "Not quite. " + q.explanation.ifBlank { "Review this idea, then try a similar question." }
                    else -> "Not quite. You can review this concept with AI at the end."
                },
                color = if (right) extras.gold else MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
    Spacer(Modifier.height(12.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        ToolButton("Previous", enabled = index > 0) { index-- }
        Spacer(Modifier.weight(1f))
        ToolButton(if (index == order.size - 1) "Finish" else "Next", primary = answer != NONE, enabled = answer != NONE) { goNext() }
    }
}

/** sendQuizReview() in web/student.html, verbatim wording. */
private fun quizSummary(tool: LearningTool.Quiz, answers: IntArray, score: Int): String {
    val detail = tool.questions.mapIndexed { i, q ->
        val selected = if (answers[i] == NONE) "No answer" else q.options[answers[i]]
        "- Q${i + 1}: ${q.prompt} | My answer: $selected | Result: ${if (answers[i] == q.answer) "correct" else "needs review"}"
    }.joinToString("\n")
    return "Quiz result summary\nActivity: ${tool.title}\nScore: $score/${tool.questions.size}\n$detail\n" +
        "Please identify the concepts I need to review, explain only what I missed, then give me one short targeted practice question."
}

// ---- flashcards -------------------------------------------------------------

@Composable
private fun Flashcards(tool: LearningTool.Flashcards) {
    val extras = LocalChronosColors.current
    val cards = tool.cards
    var deck by rememberSaveable { mutableStateOf(IntArray(cards.size) { it }) }
    var index by rememberSaveable { mutableIntStateOf(0) }
    var flipped by rememberSaveable { mutableStateOf(false) }
    var known by rememberSaveable { mutableStateOf(IntArray(0)) }
    var again by rememberSaveable { mutableStateOf(IntArray(0)) }
    var finished by rememberSaveable { mutableStateOf(false) }

    fun go(step: Int) {
        flipped = false
        if (index + step >= deck.size) finished = true else index = maxOf(0, index + step)
    }

    if (finished) {
        val remaining = cards.indices.filter { it !in known }
        Header("Flashcards", tool.title, "Complete")
        Bar(1f)
        Text("DECK COMPLETE", style = MaterialTheme.typography.labelSmall, color = extras.muted)
        CountUp(known.size, cards.size)
        Text(
            if (remaining.isNotEmpty()) "${remaining.size} ${if (remaining.size == 1) "card is" else "cards are"} still worth another pass."
            else "You got every card. Try explaining a few from memory.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (remaining.isNotEmpty()) ToolButton("Study remaining (${remaining.size})", primary = true) {
                deck = remaining.toIntArray(); index = 0; finished = false
            }
            ToolButton("Restart") {
                known = IntArray(0); again = IntArray(0); deck = IntArray(cards.size) { it }; index = 0; finished = false
            }
        }
        return
    }

    val ci = deck[index]
    val (front, back) = cards[ci]
    val rotation by animateFloatAsState(if (flipped) 180f else 0f,
        if (LocalReduceMotion.current) snap() else tween(400), label = "flip")
    Header("Flashcards", tool.title, "Card ${index + 1} of ${deck.size}")
    Bar(index.toFloat() / deck.size)
    Box(
        Modifier.fillMaxWidth().heightIn(min = 160.dp)
            .graphicsLayer { rotationY = rotation; cameraDistance = 12 * density }
            .background(if (rotation > 90f) extras.raised else MaterialTheme.colorScheme.surface)
            .border(1.dp, extras.rule)
            .clickable { flipped = !flipped }
            .padding(18.dp),
        contentAlignment = Alignment.Center,
    ) {
        // The back face is drawn mirrored, then flipped back upright.
        Column(Modifier.graphicsLayer { rotationY = if (rotation > 90f) 180f else 0f },
            horizontalAlignment = Alignment.CenterHorizontally) {
            Text(if (rotation > 90f) "ANSWER" else "PROMPT", style = MaterialTheme.typography.labelSmall, color = extras.muted)
            Spacer(Modifier.height(8.dp))
            Text(if (rotation > 90f) back else front, textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
            if (!flipped) Text("Tap to reveal", style = MaterialTheme.typography.labelSmall, color = extras.muted,
                modifier = Modifier.padding(top = 8.dp))
        }
    }
    Spacer(Modifier.height(10.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ToolButton("Still learning", enabled = flipped) {
            again = (again.toSet() + ci).toIntArray(); known = (known.toSet() - ci).toIntArray(); go(1)
        }
        ToolButton("Got it", primary = true, enabled = flipped) {
            known = (known.toSet() + ci).toIntArray(); again = (again.toSet() - ci).toIntArray(); go(1)
        }
    }
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text("Got it ${known.size} · Still learning ${again.size}", style = MaterialTheme.typography.labelSmall,
            color = extras.muted, modifier = Modifier.weight(1f))
        TextButton(onClick = { deck = deck.copyOf().also { it.shuffle() }; index = 0; flipped = false }) { Text("Shuffle") }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        ToolButton("Previous", enabled = index > 0) { go(-1) }
        Spacer(Modifier.weight(1f))
        ToolButton(if (index == deck.size - 1) "Finish" else "Next") { go(1) }
    }
}

// ---- concept map ------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ConceptMap(tool: LearningTool.ConceptMap) {
    val extras = LocalChronosColors.current
    var pulse by remember { mutableIntStateOf(NONE) }
    var pulseTick by remember { mutableIntStateOf(0) }
    LaunchedEffect(pulseTick) { if (pulse != NONE) { delay(900); pulse = NONE } }
    Header("Concept map", tool.title)
    tool.nodes.forEachIndexed { i, node ->
        val bg by animateColorAsState(if (pulse == i) extras.raised else Color.Transparent, label = "pulse")
        Column(Modifier.fillMaxWidth().padding(bottom = 10.dp).background(bg).border(1.dp, extras.rule).padding(12.dp)) {
            Text(buildString { append(node.label); if (node.detail.isNotBlank()) append(" — " + node.detail) },
                style = MaterialTheme.typography.bodyMedium)
            val out = tool.links.filter { it.from == i }
            if (out.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    out.forEach { l ->
                        Text(
                            "${l.label.ifBlank { "relates to" }} → ${tool.nodes[l.to].label}",
                            style = MaterialTheme.typography.labelSmall, color = extras.crimsonFill,
                            modifier = Modifier.border(1.dp, extras.crimsonFill)
                                .clickable { pulse = l.to; pulseTick++ }.padding(horizontal = 8.dp, vertical = 4.dp),
                        )
                    }
                }
            }
        }
    }
}

// ---- review sheet -----------------------------------------------------------

@Composable
private fun ReviewSheet(tool: LearningTool.ReviewSheet) {
    val extras = LocalChronosColors.current
    val total = tool.sections.sumOf { it.second.size }
    // "section:point" keys of ticked points; the set of collapsed section indexes.
    var done by rememberSaveable { mutableStateOf(arrayListOf<String>()) }
    var closed by rememberSaveable { mutableStateOf(arrayListOf<Int>()) }
    Header("Review sheet", tool.title)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("${done.size} of $total reviewed", style = MaterialTheme.typography.labelSmall, color = extras.muted,
            modifier = Modifier.weight(1f))
        val allClosed = closed.size == tool.sections.size
        TextButton(onClick = { closed = if (allClosed) arrayListOf() else ArrayList(tool.sections.indices.toList()) }) {
            Text(if (allClosed) "Expand all" else "Collapse all")
        }
    }
    Bar(if (total == 0) 0f else done.size.toFloat() / total)
    tool.sections.forEachIndexed { si, (heading, points) ->
        val open = si !in closed
        Row(
            Modifier.fillMaxWidth().clickable { closed = ArrayList(if (open) closed + si else closed - si) }.padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(heading, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Text("${points.indices.count { "$si:$it" in done }}/${points.size}", style = MaterialTheme.typography.labelSmall, color = extras.muted)
            Sym(if (open) "expand_less" else "expand_more", size = 18.sp, tint = extras.muted)
        }
        if (open) points.forEachIndexed { pi, point ->
            val key = "$si:$pi"
            val on = key in done
            Row(
                Modifier.fillMaxWidth().clickable { done = ArrayList(if (on) done - key else done + key) }.padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Checkbox(checked = on, onCheckedChange = null,
                    colors = CheckboxDefaults.colors(checkedColor = extras.crimsonFill))
                Spacer(Modifier.width(6.dp))
                Text(point, style = MaterialTheme.typography.bodyMedium, color = if (on) extras.muted else Color.Unspecified)
            }
        }
        HorizontalDivider(color = extras.rule)
    }
}
