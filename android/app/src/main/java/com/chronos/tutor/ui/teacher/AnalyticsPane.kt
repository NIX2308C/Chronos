package com.chronos.tutor.ui.teacher

import androidx.compose.animation.core.animateIntAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.QuestionRow
import com.chronos.tutor.data.Stats
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.LocalReduceMotion

private val RANGES = listOf(7 to "7 days", 30 to "30 days", 0 to "This term")

@Composable
fun AnalyticsPane(vm: TeacherViewModel, state: TeacherUiState) {
    val extras = LocalChronosColors.current
    state.profileFor?.let { m ->
        AlertDialog(
            onDismissRequest = vm::closeProfile, shape = RectangleShape,
            title = { Text(m.email.ifBlank { "Student" }) },
            text = {
                val p = state.profile
                when {
                    p == null -> Loading(true)
                    p.summary.isBlank() -> Text(
                        "Not enough messages yet (${p.messages} of ${p.minMessages}) to say anything useful.",
                        color = extras.muted,
                    )
                    else -> Column {
                        Text(p.summary)
                        Text("${p.messages} messages", style = MaterialTheme.typography.bodySmall, color = extras.muted)
                        if (p.stickingPoints.isNotEmpty()) {
                            Spacer(Modifier.height(10.dp))
                            Text("Sticking points", style = MaterialTheme.typography.labelSmall, color = extras.muted)
                            p.stickingPoints.forEach { (topic, n) -> Text("• $topic ($n×)", style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = vm::closeProfile) { Text("Close") } },
        )
    }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                RANGES.forEach { (days, label) ->
                    FilterChip(
                        selected = state.range == days, onClick = { vm.setRange(days) },
                        label = { Text(label) }, shape = RectangleShape,
                        modifier = Modifier.padding(end = 8.dp),
                    )
                }
                Spacer(Modifier.weight(1f))
                IconButton(onClick = { vm.loadAnalytics() }, enabled = !state.statsLoading) {
                    Sym("refresh", size = 20.sp, tint = extras.muted)
                }
            }
            if (state.statsLoading) LinearProgressIndicator(Modifier.fillMaxWidth(), color = extras.crimsonFill)
        }
        val s = state.stats
        if (s == null) {
            if (!state.statsLoading) item { Text("Couldn't load analytics. Tap refresh to try again.", color = extras.muted) }
        } else {
            item { Kpis(s, state.range) }
            item {
                Section("Not covered by your material") {
                    Rows(s.unanswered, "No gaps in this window.") {
                        TextButton(onClick = { vm.setSection(TeacherSection.MATERIAL); vm.setTab("material") }) {
                            Text("Add material", color = extras.crimsonFill)
                        }
                    }
                }
            }
            item { Section("Learning signals") { Rows(s.learningGaps, "None in this window.", unit = "signal") } }
            item { Section("Conduct concerns") { Rows(s.behaviorConcerns, "None in this window.", unit = "signal") } }
            item { Section("Topics") { Topics(s.categories) } }
            item {
                Section("Asked repeatedly") {
                    Rows(s.repeats.filter { it.count > 1 }, "Nothing asked twice yet.")
                    Text("Questions are never attributed to a student.", style = MaterialTheme.typography.bodySmall, color = extras.muted)
                }
            }
        }
        item {
            Section("Students") {
                if (state.roster.isEmpty()) Text("No students have joined yet.", color = extras.muted)
                state.roster.forEach { m ->
                    Row(
                        Modifier.fillMaxWidth().clickable { vm.openProfile(m) }.padding(vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Sym("person", size = 18.sp, tint = extras.muted)
                        Spacer(Modifier.width(10.dp))
                        Text(m.email.ifBlank { m.uid }, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                        Sym("chevron_right", size = 18.sp, tint = extras.muted)
                    }
                }
            }
        }
    }
}

@Composable
private fun Kpis(s: Stats, range: Int) {
    val extras = LocalChronosColors.current
    val grounded = if (s.totalQuestions > 0) "${Math.round(s.grounded * 100f / s.totalQuestions)}%" else "—"
    Card {
        Row {
            Kpi("Questions asked", s.totalQuestions,
                if (range > 0) "conversations in the last ${plural(range, "day")}" else "conversations logged so far", Modifier.weight(1f))
            Kpi("Students active", s.studentsActive, "of ${plural(s.studentsTotal, "student")} in the course", Modifier.weight(1f))
        }
        Spacer(Modifier.height(16.dp))
        Row {
            Column(Modifier.weight(1f)) {
                Text("Answered from material", style = MaterialTheme.typography.bodySmall, color = extras.muted)
                Text(grounded, fontSize = 30.sp, fontWeight = FontWeight.SemiBold)
            }
            Kpi("Gaps to close", s.unansweredCount, null, Modifier.weight(1f), extras.crimsonFill)
        }
    }
}

@Composable
private fun Kpi(label: String, value: Int, sub: String?, modifier: Modifier, color: Color = Color.Unspecified) {
    val extras = LocalChronosColors.current
    // Count-up, as the web's animateCount.
    var target by remember { mutableIntStateOf(0) }
    LaunchedEffect(value) { target = value }
    val shown by animateIntAsState(target, if (LocalReduceMotion.current) snap() else tween(700), label = label)
    Column(modifier) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = extras.muted)
        Text("$shown", fontSize = 30.sp, fontWeight = FontWeight.SemiBold, color = color)
        sub?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = extras.muted) }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) = Card {
    Text(title.uppercase(), style = MaterialTheme.typography.labelSmall, color = LocalChronosColors.current.muted)
    Spacer(Modifier.height(8.dp))
    content()
}

@Composable
private fun ColumnScope.Rows(
    rows: List<QuestionRow>, empty: String, unit: String = "time",
    trailing: (@Composable () -> Unit)? = null,
) {
    val extras = LocalChronosColors.current
    if (rows.isEmpty()) {
        Text(empty, color = extras.muted, style = MaterialTheme.typography.bodyMedium); return
    }
    rows.forEach { r ->
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(r.question, style = MaterialTheme.typography.bodyMedium)
                Text("${plural(r.students, "student")} · ${plural(r.count, unit)}",
                    style = MaterialTheme.typography.labelSmall, color = extras.muted)
            }
            trailing?.invoke()
        }
        HorizontalDivider(color = extras.rule)
    }
}

@Composable
private fun Topics(categories: List<Pair<String, Int>>) {
    val extras = LocalChronosColors.current
    if (categories.isEmpty()) { Text("No questions in this window yet.", color = extras.muted); return }
    val max = categories.maxOf { it.second }.coerceAtLeast(1)
    categories.forEach { (name, n) ->
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            Text(name, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            Text("$n", style = MaterialTheme.typography.bodySmall, color = extras.muted)
        }
        Box(Modifier.fillMaxWidth().height(6.dp).background(extras.raised)) {
            Box(Modifier.fillMaxWidth(n / max.toFloat()).fillMaxHeight().background(extras.crimsonFill))
        }
    }
}

private fun plural(n: Int, word: String) = "$n $word" + if (n == 1) "" else "s"
