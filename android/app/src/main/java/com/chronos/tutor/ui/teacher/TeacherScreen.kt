package com.chronos.tutor.ui.teacher

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.CourseClass
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TeacherScreen(
    vm: TeacherViewModel,
    state: TeacherUiState,
    onPreview: () -> Unit,
    onSettings: () -> Unit,
    onHelp: () -> Unit,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var creating by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<CourseClass?>(null) }

    LaunchedEffect(state.notice) {
        val n = state.notice ?: return@LaunchedEffect
        snackbar.showSnackbar(n.text, duration = if (n.error) SnackbarDuration.Long else SnackbarDuration.Short)
        vm.dismissNotice()
    }

    if (state.booting) {
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background), Alignment.Center) {
            CircularProgressIndicator(color = extras.crimsonFill, strokeWidth = 2.dp)
        }
        return
    }
    if (state.bootError != null) {
        CenterMessage(state.bootError, "Try again", vm::boot, onSignOut)
        return
    }

    if (creating) NameDialog(
        title = "New course", label = "Course name", confirm = "Create",
        onDismiss = { creating = false },
        onConfirm = { creating = false; vm.createCourse(it) },
    )
    deleting?.let { c ->
        ConfirmDialog(
            title = "Delete \"${c.name}\"?",
            body = "This removes its material, rules and student list. It cannot be undone.",
            confirm = "Delete",
            onDismiss = { deleting = null },
            onConfirm = { deleting = null; vm.deleteCourse(c.id) },
        )
    }

    // A teacher with no courses gets a real way forward, not a pointer to the web.
    if (state.classes.isEmpty()) {
        Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
            Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(24.dp), Alignment.Center) {
                Column(
                    Modifier.widthIn(max = 420.dp).fillMaxWidth()
                        .hardShadow(extras.crimsonFill, 7.dp)
                        .background(MaterialTheme.colorScheme.surfaceContainerLow)
                        .border(1.dp, extras.rule).padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Sym("school", size = 30.sp, tint = extras.crimsonFill)
                    Spacer(Modifier.height(14.dp))
                    Text("Make your first course", style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Create a course, add your material, then share its join code with students.",
                        style = MaterialTheme.typography.bodyMedium, color = extras.muted, textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(18.dp))
                    PrimaryButton("CREATE COURSE", Modifier.fillMaxWidth()) { creating = true }
                    TextButton(onClick = onSignOut, shape = RectangleShape) { Text("Sign out", color = extras.muted) }
                }
            }
        }
        return
    }

    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            TeacherDrawer(
                state = state,
                onSelectCourse = { scope.launch { drawer.close() }; vm.selectCourse(it) },
                onCreate = { creating = true },
                onDelete = { deleting = it },
                onSection = { scope.launch { drawer.close() }; vm.setSection(it) },
                onPreview = { scope.launch { drawer.close() }; onPreview() },
                onSettings = { scope.launch { drawer.close() }; onSettings() },
                onSignOut = onSignOut,
            )
        },
    ) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            snackbarHost = { SnackbarHost(snackbar) },
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawer.open() } }) {
                            Sym("menu", tint = MaterialTheme.colorScheme.onSurface)
                        }
                    },
                    actions = {
                        IconButton(onClick = onHelp) { Sym("help", tint = extras.muted) }
                    },
                    title = {
                        Column {
                            Text(
                                if (state.section == TeacherSection.MATERIAL) "Course material" else "Analytics",
                                style = MaterialTheme.typography.labelLarge, maxLines = 1,
                            )
                            Text(
                                state.active?.name.orEmpty(), style = MaterialTheme.typography.bodySmall,
                                color = extras.muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    },
                )
            },
        ) { padding ->
            Box(Modifier.padding(padding).fillMaxSize()) {
                when (state.section) {
                    TeacherSection.MATERIAL -> MaterialPane(vm, state)
                    TeacherSection.ANALYTICS -> AnalyticsPane(vm, state)
                }
            }
        }
    }
}

@Composable
private fun TeacherDrawer(
    state: TeacherUiState,
    onSelectCourse: (String) -> Unit,
    onCreate: () -> Unit,
    onDelete: (CourseClass) -> Unit,
    onSection: (TeacherSection) -> Unit,
    onPreview: () -> Unit,
    onSettings: () -> Unit,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf<String?>(null) }

    ModalDrawerSheet(
        drawerShape = RectangleShape,
        drawerContainerColor = MaterialTheme.colorScheme.surfaceContainer,
        modifier = Modifier.width(300.dp),
    ) {
        Column(Modifier.fillMaxSize()) {
            Text("CHRONOS · TEACHER", style = MaterialTheme.typography.labelSmall, color = extras.muted,
                modifier = Modifier.padding(18.dp))

            NavRow("folder_open", "Course material", state.section == TeacherSection.MATERIAL) { onSection(TeacherSection.MATERIAL) }
            NavRow("insights", "Analytics", state.section == TeacherSection.ANALYTICS) { onSection(TeacherSection.ANALYTICS) }
            NavRow("visibility", "Preview as student", false, onPreview)

            HorizontalDivider(color = extras.rule, modifier = Modifier.padding(top = 8.dp))
            Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 6.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("COURSES", style = MaterialTheme.typography.labelSmall, color = extras.muted, modifier = Modifier.weight(1f))
                IconButton(onClick = onCreate) { Sym("add", size = 18.sp, tint = extras.crimsonFill) }
            }

            LazyColumn(Modifier.weight(1f)) {
                items(state.classes.size) { i ->
                    val c = state.classes[i]
                    val active = c.id == state.activeClassId
                    Row(
                        Modifier.fillMaxWidth()
                            .background(if (active) extras.raised else Color.Transparent)
                            .clickable { onSelectCourse(c.id) }
                            .padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Box(Modifier.width(3.dp).height(34.dp).background(if (active) extras.crimsonFill else Color.Transparent))
                        Spacer(Modifier.width(13.dp))
                        Column(Modifier.weight(1f)) {
                            Text(c.name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                color = if (active) MaterialTheme.colorScheme.onSurface else extras.muted)
                            c.joinCode?.let { code ->
                                Text(
                                    if (copied == code) "Copied" else "Code $code",
                                    style = MaterialTheme.typography.labelSmall, color = extras.crimsonFill,
                                    modifier = Modifier.clickable { clipboard.setText(AnnotatedString(code)); copied = code },
                                )
                            }
                        }
                        IconButton(onClick = { onDelete(c) }) { Sym("delete", size = 17.sp, tint = extras.muted) }
                    }
                }
            }

            HorizontalDivider(color = extras.rule)
            NavRow("settings", "Settings", false, onSettings)
            NavRow("logout", "Sign out", false, onSignOut)
        }
    }
}

@Composable
private fun NavRow(icon: String, label: String, active: Boolean, onClick: () -> Unit) {
    val extras = LocalChronosColors.current
    Row(
        Modifier.fillMaxWidth().background(if (active) extras.raised else Color.Transparent)
            .clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Sym(icon, size = 18.sp, tint = if (active) extras.crimsonFill else extras.muted)
        Spacer(Modifier.width(12.dp))
        Text(label, style = MaterialTheme.typography.bodyMedium,
            color = if (active) MaterialTheme.colorScheme.onSurface else extras.muted)
    }
}

// ---- small shared pieces ----------------------------------------------------

@Composable
internal fun PrimaryButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    val extras = LocalChronosColors.current
    Button(
        onClick = onClick, enabled = enabled, shape = RectangleShape,
        colors = ButtonDefaults.buttonColors(containerColor = extras.crimsonFill, contentColor = MaterialTheme.colorScheme.onPrimary),
        modifier = modifier.height(46.dp),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

@Composable
internal fun CenterMessage(text: String, action: String, onAction: () -> Unit, onSignOut: () -> Unit) {
    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(24.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text, textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onBackground)
        Spacer(Modifier.height(16.dp))
        PrimaryButton(action.uppercase(), onClick = onAction)
        TextButton(onClick = onSignOut) { Text("Sign out") }
    }
}

@Composable
internal fun NameDialog(
    title: String, label: String, confirm: String,
    initial: String = "", singleLine: Boolean = true,
    onDismiss: () -> Unit, onConfirm: (String) -> Unit,
) {
    var value by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RectangleShape,
        title = { Text(title) },
        text = {
            OutlinedTextField(
                value = value, onValueChange = { value = it }, label = { Text(label) },
                singleLine = singleLine, minLines = if (singleLine) 1 else 3, shape = RectangleShape,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onConfirm(value) }, enabled = value.isNotBlank()) { Text(confirm) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
internal fun ConfirmDialog(title: String, body: String, confirm: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RectangleShape,
        title = { Text(title) },
        text = { Text(body) },
        confirmButton = {
            TextButton(onClick = onConfirm) { Text(confirm, color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
