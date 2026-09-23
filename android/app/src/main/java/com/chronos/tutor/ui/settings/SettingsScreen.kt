package com.chronos.tutor.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.chronos.tutor.data.Me
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors

/** PERSONALITY_NOTES in web/settings.js. */
private val PERSONALITY_NOTES = mapOf(
    "default" to "Balanced and clear.", "encouraging" to "Warm, notices progress.",
    "concise" to "Short and to the point.", "socratic" to "Guides with questions.",
    "casual" to "Relaxed study partner.", "vibetastic" to "Dev only. Unhinged 2023 chatbot.",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    vm: SettingsViewModel,
    me: Me,
    /** Opened from the tutor chat: the web shows the Tutor pane only there. */
    tutor: Boolean,
    onBack: () -> Unit,
    onStatus: () -> Unit,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    val ui by vm.state.collectAsStateWithLifecycle()
    val theme by vm.prefsStore.theme.collectAsStateWithLifecycle("system")
    val textSize by vm.prefsStore.textSize.collectAsStateWithLifecycle("normal")
    val reduceMotion by vm.prefsStore.reduceMotion.collectAsStateWithLifecycle(false)
    val debug by vm.prefsStore.debug.collectAsStateWithLifecycle(false)
    val snackbar = remember { SnackbarHostState() }
    var confirmClear by remember { mutableStateOf(false) }
    var flash by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        if (tutor) vm.loadTutor()
        if (me.isDev) vm.loadDeployment()
    }
    LaunchedEffect(flash) { flash?.let { snackbar.showSnackbar(it); flash = null } }

    if (confirmClear) AlertDialog(
        onDismissRequest = { confirmClear = false }, shape = RectangleShape,
        title = { Text("Delete every conversation?") },
        text = { Text("Every conversation in every course. This can't be undone.") },
        confirmButton = {
            TextButton(onClick = { confirmClear = false; vm.deleteAllChats { flash = it } }) {
                Text("Delete all", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Cancel") } },
    )

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
                navigationIcon = { IconButton(onClick = onBack) { Sym("arrow_back", tint = MaterialTheme.colorScheme.onSurface) } },
                title = { Text("Settings", style = MaterialTheme.typography.labelLarge) },
            )
        },
    ) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (tutor) Section("Tutor") {
                val p = ui.prefs
                if (p == null) {
                    Text(ui.saved ?: "Loading…", color = extras.muted)
                } else {
                    Label("Personality", PERSONALITY_NOTES[p.personality])
                    Choice(ui.personalities.map { it.id to it.label }, p.personality) { vm.updateTutor(p.copy(personality = it)) }
                    Label("Answer length")
                    Seg(listOf("short" to "Short", "balanced" to "Balanced", "detailed" to "Detailed"), p.length) {
                        vm.updateTutor(p.copy(length = it))
                    }
                    Label("Reading level")
                    Seg(listOf("auto" to "Auto", "simple" to "Simple", "standard" to "Standard", "advanced" to "Advanced"), p.readingLevel) {
                        vm.updateTutor(p.copy(readingLevel = it))
                    }
                    Toggle("Explain step by step", "For when a topic is new to you.", p.explainSimply) {
                        vm.updateTutor(p.copy(explainSimply = it))
                    }
                    OutlinedTextField(
                        p.language, { vm.updateTutor(p.copy(language = it.take(40))) },
                        label = { Text("Reply language") }, placeholder = { Text("Same as my message") },
                        singleLine = true, shape = RectangleShape, modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        p.styleNote, { vm.updateTutor(p.copy(styleNote = it.take(300))) },
                        label = { Text("Style note") },
                        placeholder = { Text("Anything else about how you like answers written") },
                        supportingText = { Text("Affects tone only. It can't change your course's rules.") },
                        minLines = 2, shape = RectangleShape, modifier = Modifier.fillMaxWidth(),
                    )
                    ui.saved?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = extras.muted) }
                }
            }

            Section("Appearance") {
                Label("Theme")
                Seg(listOf("light" to "Light", "dark" to "Dark", "system" to "System"), theme) { vm.setTheme(it) }
                Label("Text size")
                Seg(listOf("normal" to "Normal", "large" to "Large"), textSize) { vm.setTextSize(it) }
                Toggle("Reduce motion", "Turns off animations.", reduceMotion) { vm.setReduceMotion(it) }
            }

            Section("Account") {
                Row { Text("Signed in as", Modifier.weight(1f), color = extras.muted); Text(me.email.orEmpty()) }
                Spacer(Modifier.height(6.dp))
                Row { Text("Role", Modifier.weight(1f), color = extras.muted); Text(me.role) }
                Spacer(Modifier.height(10.dp))
                ActionRow("System status", "See the current availability of Chronos services.", "View status", onClick = onStatus)
                ActionRow("How it works", null, "Replay tour") { vm.replayTour(me.role == "teacher"); onBack() }
                if (tutor) ActionRow("Conversations", null, if (ui.clearing) "Deleting…" else "Delete all", danger = true) {
                    if (!ui.clearing) confirmClear = true
                }
                ActionRow("Sign out", null, "Sign out", onClick = onSignOut)
            }

            if (me.isDev) Section("Developer") {
                Toggle("Debug mode", "Timings, retrieval and prompt size under each tutor answer.", debug) { vm.setDebug(it) }
                Label("Cloud deployment")
                val d = ui.deployment
                if (d == null) Text("Loading…", color = extras.muted)
                else d.forEach { (k, v) ->
                    Row { Text(k, Modifier.weight(1f), color = extras.muted, style = MaterialTheme.typography.bodySmall)
                        Text(v, style = MaterialTheme.typography.bodySmall) }
                }
                TextButton(onClick = { vm.loadDeployment() }) { Text("Refresh") }
            }
        }
    }
}

// ---- pieces -----------------------------------------------------------------

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    val extras = LocalChronosColors.current
    Column(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).border(1.dp, extras.rule).padding(16.dp)
    ) {
        Text(title.uppercase(), style = MaterialTheme.typography.labelSmall, color = extras.muted)
        Spacer(Modifier.height(8.dp))
        content()
    }
}

@Composable
private fun Label(text: String, note: String? = null) {
    Spacer(Modifier.height(10.dp))
    Text(text, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
    note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = LocalChronosColors.current.muted) }
    Spacer(Modifier.height(6.dp))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Seg(options: List<Pair<String, String>>, selected: String, onPick: (String) -> Unit) {
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        options.forEachIndexed { i, (key, label) ->
            SegmentedButton(
                selected = selected == key, onClick = { onPick(key) },
                shape = SegmentedButtonDefaults.itemShape(i, options.size, RoundedCornerShape(0.dp)),
                label = { Text(label, fontSize = 12.sp, maxLines = 1) },
            )
        }
    }
}

@Composable
private fun Choice(options: List<Pair<String, String>>, selected: String, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val extras = LocalChronosColors.current
    Box {
        Row(
            Modifier.fillMaxWidth().border(1.dp, extras.rule).clickable { open = true }.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(options.firstOrNull { it.first == selected }?.second ?: selected, Modifier.weight(1f))
            Sym("expand_more", size = 18.sp, tint = extras.muted)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { (id, label) ->
                DropdownMenuItem(text = { Text(label) }, onClick = { open = false; onPick(id) })
            }
        }
    }
}

@Composable
private fun Toggle(title: String, hint: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    val extras = LocalChronosColors.current
    Row(Modifier.fillMaxWidth().clickable { onChange(!checked) }.padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = extras.muted) }
        }
        Switch(checked, onChange, colors = SwitchDefaults.colors(checkedTrackColor = extras.crimsonFill))
    }
}

@Composable
private fun ActionRow(title: String, hint: String?, action: String, danger: Boolean = false, onClick: () -> Unit) {
    val extras = LocalChronosColors.current
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
            hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = extras.muted) }
        }
        OutlinedButton(onClick = onClick, shape = RectangleShape) {
            Text(action, color = if (danger) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
        }
    }
}
