package com.chronos.tutor.ui.teacher

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.CourseDoc
import com.chronos.tutor.data.CourseSettings
import com.chronos.tutor.data.CustomRule
import com.chronos.tutor.ui.common.DOC_MIME_TYPES
import com.chronos.tutor.ui.common.MarkdownText
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

private val TABS = listOf("material" to "Course material", "rules" to "Tutor rules", "tools" to "Toolkits")

@Composable
fun MaterialPane(vm: TeacherViewModel, state: TeacherUiState) {
    val tabIndex = TABS.indexOfFirst { it.first == state.tab }.coerceAtLeast(0)
    Column(Modifier.fillMaxSize()) {
        ScrollableTabRow(
            selectedTabIndex = tabIndex, edgePadding = 12.dp,
            containerColor = MaterialTheme.colorScheme.background,
            contentColor = LocalChronosColors.current.crimsonFill,
        ) {
            TABS.forEachIndexed { i, (key, label) ->
                Tab(selected = i == tabIndex, onClick = { vm.setTab(key) }, text = { Text(label) })
            }
        }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            when (state.tab) {
                "rules" -> rulesTab(vm, state)
                "tools" -> toolsTab(vm, state)
                else -> materialTab(vm, state)
            }
        }
    }
}

// ---- course material --------------------------------------------------------

private fun LazyListScope.materialTab(vm: TeacherViewModel, state: TeacherUiState) {
    item { UploadCard(vm, state) }
    item { ProbeCard(vm, state) }
    val m = state.material
    if (m == null) {
        item { Loading(state.materialLoading) }
        return
    }
    item { DocList(m.docs, m.docsTruncated, vm::deleteDoc) }
}

@Composable
private fun UploadCard(vm: TeacherViewModel, state: TeacherUiState) {
    val resolver = LocalContext.current.contentResolver
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) vm.upload(resolver, uri)
    }
    // /upload has no progress events, so the stage just names the step the
    // server is likely on by then (uploadFile() in teacherknowledge.html).
    var stage by remember { mutableStateOf("Uploading") }
    LaunchedEffect(state.uploading) {
        if (state.uploading == null) return@LaunchedEffect
        stage = "Uploading"; delay(1_500)
        stage = "Reading through it"; delay(3_500)
        stage = "Getting ready for questions"
    }
    Card {
        Text("Add material", style = MaterialTheme.typography.titleMedium)
        Text("PDF, Word, text, Markdown or CSV, up to 10 MB. The tutor answers from what you add here.",
            style = MaterialTheme.typography.bodySmall, color = LocalChronosColors.current.muted)
        Spacer(Modifier.height(12.dp))
        if (state.uploading != null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(state.uploading, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(12.dp))
                Text(stage, style = MaterialTheme.typography.bodySmall, color = LocalChronosColors.current.muted)
            }
            Spacer(Modifier.height(8.dp))
            LinearProgressIndicator(Modifier.fillMaxWidth(), color = LocalChronosColors.current.crimsonFill)
        } else {
            PrimaryButton("CHOOSE FILE", Modifier.fillMaxWidth()) { pick.launch(DOC_MIME_TYPES) }
        }
    }
}

@Composable
private fun ProbeCard(vm: TeacherViewModel, state: TeacherUiState) {
    val extras = LocalChronosColors.current
    var q by rememberSaveable(state.activeClassId) { mutableStateOf("") }
    Card {
        Text("Check what the course can answer", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = q, onValueChange = { q = it }, label = { Text("Ask a student question") },
            shape = RectangleShape, modifier = Modifier.fillMaxWidth(), enabled = !state.probing,
        )
        Spacer(Modifier.height(8.dp))
        PrimaryButton(if (state.probing) "CHECKING…" else "CHECK", enabled = !state.probing && q.isNotBlank()) { vm.probe(q) }
        state.probeResult?.let { r ->
            Spacer(Modifier.height(12.dp))
            HorizontalDivider(color = extras.rule)
            Spacer(Modifier.height(12.dp))
            if (r.materialGap) {
                Text("Recorded as a gap: nothing in your material matched.", color = extras.crimsonFill,
                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(6.dp))
            }
            MarkdownText(r.response)
            if (r.sources.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text("Sources used", style = MaterialTheme.typography.labelSmall, color = extras.muted)
                r.sources.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = extras.muted) }
            }
        }
    }
}

@Composable
private fun DocList(docs: List<CourseDoc>, truncated: Boolean, onDelete: (CourseDoc) -> Unit) {
    val extras = LocalChronosColors.current
    var query by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf<CourseDoc?>(null) }
    confirm?.let { d ->
        ConfirmDialog(
            title = "Delete \"${d.name}\"?",
            body = "${d.chunks} chunk${if (d.chunks == 1) "" else "s"}. This cannot be undone.",
            confirm = "Delete", onDismiss = { confirm = null }, onConfirm = { confirm = null; onDelete(d) },
        )
    }
    var sort by remember { mutableStateOf("new") }
    val matched = if (query.isBlank()) docs else docs.filter { d ->
        (d.name + " " + d.summary.orEmpty() + " " + d.topics.joinToString(" ")).contains(query.trim(), ignoreCase = true)
    }
    val shown = when (sort) {
        "old" -> matched.sortedBy { it.addedMs ?: 0L }
        "name" -> matched.sortedWith(compareBy(NATURAL) { it.name })
        "size" -> matched.sortedByDescending { it.chunks }
        else -> matched   // already newest first
    }
    Card {
        Text("Documents (${docs.size})", style = MaterialTheme.typography.titleMedium)
        // Search only once there are enough documents to need it, as on the web.
        if (docs.size >= 8) {
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(query, { query = it }, label = { Text("Search documents") }, singleLine = true,
                shape = RectangleShape, modifier = Modifier.fillMaxWidth())
            Row(Modifier.horizontalScroll(rememberScrollState()).padding(top = 6.dp)) {
                DOC_SORTS.forEach { (key, label) ->
                    FilterChip(selected = sort == key, onClick = { sort = key }, label = { Text(label) },
                        shape = RectangleShape, modifier = Modifier.padding(end = 6.dp))
                }
            }
        }
        if (truncated) {
            Text("Showing the first part of a very large course.", style = MaterialTheme.typography.bodySmall, color = extras.muted)
        }
        Spacer(Modifier.height(4.dp))
        if (shown.isEmpty()) {
            Text(if (docs.isEmpty()) "No documents yet." else "No matching documents.",
                style = MaterialTheme.typography.bodyMedium, color = extras.muted, modifier = Modifier.padding(vertical = 8.dp))
        }
        shown.forEach { d ->
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Sym(docIcon(d.name), size = 21.sp, tint = extras.muted)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(d.name, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(d.summary ?: added(d.addedMs), style = MaterialTheme.typography.bodySmall, color = extras.muted,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (d.topics.isNotEmpty()) {
                        Text(d.topics.take(5).joinToString(" · "), style = MaterialTheme.typography.labelSmall,
                            color = extras.crimsonFill, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    Text("${d.chunks} chunk${if (d.chunks == 1) "" else "s"}", style = MaterialTheme.typography.labelSmall, color = extras.muted)
                }
                IconButton(onClick = { confirm = d }) { Sym("delete", size = 19.sp, tint = extras.muted) }
            }
        }
    }
}

private val DOC_SORTS = listOf("new" to "Newest", "old" to "Oldest", "name" to "Name", "size" to "Largest")

/** localeCompare(..., {numeric: true}) on the web: "Week 2" before "Week 10". */
private val NATURAL = Comparator<String> { a, b ->
    val chunk = Regex("\\d+|\\D+")
    val xs = chunk.findAll(a.lowercase()).map { it.value }.toList()
    val ys = chunk.findAll(b.lowercase()).map { it.value }.toList()
    for (i in 0 until minOf(xs.size, ys.size)) {
        val x = xs[i]; val y = ys[i]
        val c = if (x[0].isDigit() && y[0].isDigit()) x.toBigInteger().compareTo(y.toBigInteger()) else x.compareTo(y)
        if (c != 0) return@Comparator c
    }
    xs.size - ys.size
}

private fun docIcon(name: String) = when (name.substringAfterLast('.', "").lowercase()) {
    "pdf" -> "picture_as_pdf"
    "csv" -> "table_chart"
    else -> "description"
}

/** fmtWhen() in web/teacherknowledge.html. */
private fun added(ms: Long?): String {
    if (ms == null) return ""
    val days = ((System.currentTimeMillis() - ms) / 86_400_000L).toInt()
    return when {
        days <= 0 -> "Added today"
        days == 1 -> "Added yesterday"
        days < 7 -> "Added $days days ago"
        else -> "Added " + DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date(ms))
    }
}

// ---- tutor rules ------------------------------------------------------------

private fun LazyListScope.rulesTab(vm: TeacherViewModel, state: TeacherUiState) {
    val s = state.settings
    item {
        Card {
            Text("Base rules", style = MaterialTheme.typography.titleMedium)
            if (s == null) { Loading(true); return@Card }
            val set = vm::updateSettings
            SettingRow("Only use my course material",
                "Facts come from your material. The tutor still chats and points students to what is covered.",
                s.groundedOnly) { set(s.copy(groundedOnly = it)) }
            if (!s.groundedOnly) {
                Text(
                    "Chronos may now use general knowledge and will label it as outside the course material.",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth().background(LocalChronosColors.current.raised).padding(10.dp),
                )
            }
            SettingRow("Guide, don't complete work", null, s.guideNotComplete) { set(s.copy(guideNotComplete = it)) }
            SettingRow("Say when unsure", null, s.stateUncertainty) { set(s.copy(stateUncertainty = it)) }
            SettingRow("Reveal final answers", null, s.revealFinalAnswers) { set(s.copy(revealFinalAnswers = it)) }
            SettingRow("Allow worked examples", null, s.workedExamples) { set(s.copy(workedExamples = it)) }
            Spacer(Modifier.height(8.dp))
            Text("Hint strength", fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(6.dp))
            HintStrength(s) { set(s.copy(hintStrength = it)) }
        }
    }
    item { CustomRules(vm, state) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HintStrength(s: CourseSettings, onPick: (String) -> Unit) {
    val options = listOf("light" to "Light", "progressive" to "Progressive", "strong" to "Strong")
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        options.forEachIndexed { i, (key, label) ->
            SegmentedButton(
                selected = s.hintStrength == key, onClick = { onPick(key) },
                shape = SegmentedButtonDefaults.itemShape(i, options.size, RoundedCornerShape(0.dp)),
            ) { Text(label) }
        }
    }
}

@Composable
private fun CustomRules(vm: TeacherViewModel, state: TeacherUiState) {
    val extras = LocalChronosColors.current
    var draft by rememberSaveable(state.activeClassId) { mutableStateOf("") }
    var ruleQuery by rememberSaveable(state.activeClassId) { mutableStateOf("") }
    var editing by remember { mutableStateOf<CustomRule?>(null) }
    var deleting by remember { mutableStateOf<CustomRule?>(null) }
    editing?.let { r ->
        NameDialog("Edit rule", "Rule", "Save", initial = r.text, singleLine = false,
            onDismiss = { editing = null }, onConfirm = { editing = null; vm.editRule(r, it) })
    }
    deleting?.let { r ->
        ConfirmDialog("Delete this rule?", r.text, "Delete", { deleting = null }, { deleting = null; vm.deleteRule(r.id) })
    }
    Card {
        Text("Custom rules", style = MaterialTheme.typography.titleMedium)
        Text("Add each line saves one rule per line.", style = MaterialTheme.typography.bodySmall, color = extras.muted)
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(draft, { draft = it }, label = { Text("e.g. Always ask what the student has tried first") },
            minLines = 2, shape = RectangleShape, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            PrimaryButton("ADD", enabled = draft.isNotBlank()) { vm.addRule(draft); draft = "" }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { vm.addRules(draft); draft = "" }, enabled = draft.isNotBlank(),
                shape = RectangleShape, modifier = Modifier.height(46.dp)) { Text("ADD EACH LINE") }
        }
        Spacer(Modifier.height(8.dp))
        val all = state.material?.rules
        // Search appears once there are enough rules to need it (RULE_SEARCH_MIN on the web).
        if (all != null && (all.size >= 8 || ruleQuery.isNotBlank())) {
            OutlinedTextField(ruleQuery, { ruleQuery = it }, label = { Text("Search rules") }, singleLine = true,
                shape = RectangleShape, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(4.dp))
        }
        val rules = all?.filter { ruleQuery.isBlank() || it.text.contains(ruleQuery.trim(), ignoreCase = true) }
        when {
            rules == null -> Loading(state.materialLoading)
            rules.isEmpty() -> Text(if (all.isNullOrEmpty()) "No custom rules yet." else "No matching rules.",
                color = extras.muted, style = MaterialTheme.typography.bodyMedium)
            else -> rules.forEach { r ->
                Row(Modifier.fillMaxWidth().clickable { editing = r }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(r.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    IconButton(onClick = { editing = r }) { Sym("edit", size = 18.sp, tint = extras.muted) }
                    IconButton(onClick = { deleting = r }) { Sym("delete", size = 18.sp, tint = extras.muted) }
                }
            }
        }
    }
}

// ---- toolkits ---------------------------------------------------------------

private fun LazyListScope.toolsTab(vm: TeacherViewModel, state: TeacherUiState) {
    item {
        Card {
            Text("Toolkits", style = MaterialTheme.typography.titleMedium)
            Text("Learning activities students can ask the tutor for.", style = MaterialTheme.typography.bodySmall,
                color = LocalChronosColors.current.muted)
            val s = state.settings
            if (s == null) { Loading(true); return@Card }
            val set = vm::updateSettings
            SettingRow("Practice", "Quizzes and flashcards", s.practiceTools) { set(s.copy(practiceTools = it)) }
            SettingRow("Visual", "Concept maps", s.visualTools) { set(s.copy(visualTools = it)) }
            SettingRow("Study", "Review sheets", s.studyMaterials) { set(s.copy(studyMaterials = it)) }
        }
    }
}

// ---- pieces -----------------------------------------------------------------

@Composable
internal fun Card(content: @Composable ColumnScope.() -> Unit) {
    val extras = LocalChronosColors.current
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, extras.rule).padding(16.dp),
        content = content,
    )
}

@Composable
private fun SettingRow(title: String, hint: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    val extras = LocalChronosColors.current
    Row(
        Modifier.fillMaxWidth().clickable { onChange(!checked) }.padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = extras.muted) }
        }
        Switch(
            checked = checked, onCheckedChange = onChange,
            colors = SwitchDefaults.colors(checkedTrackColor = extras.crimsonFill),
        )
    }
}

@Composable
internal fun Loading(active: Boolean) {
    if (!active) return
    Box(Modifier.fillMaxWidth().padding(16.dp), Alignment.Center) {
        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = LocalChronosColors.current.crimsonFill)
    }
}
