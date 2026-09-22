package com.chronos.tutor.ui.student

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.Chat
import com.chronos.tutor.data.Message
import com.chronos.tutor.data.ServerStatus
import com.chronos.tutor.data.StudentFile
import com.chronos.tutor.data.toolLabel
import com.chronos.tutor.ui.common.DOC_MIME_TYPES
import com.chronos.tutor.ui.common.MarkdownText
import com.chronos.tutor.ui.common.Sounds
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow
import kotlinx.coroutines.launch

/** The three starter prompts on an empty thread (web/student.html:604). */
private val STARTERS = listOf(
    "Explain the main idea in simple terms",
    "Give me an example from the material",
    "What should I revise first?",
)

@Composable
fun ChatScreen(
    state: ChatUiState,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onSelectChat: (String) -> Unit,
    onDeleteChat: (String) -> Unit,
    onNewChat: () -> Unit,
    onSwitchClass: (String) -> Unit,
    onJoin: (String) -> Unit,
    onSignOut: () -> Unit,
    onRetry: () -> Unit,
    onDismissError: () -> Unit,
    onTool: (String) -> Unit,
    onStopTool: () -> Unit,
    onSound: (Sounds.Kind) -> Unit,
    onReview: (String) -> Unit,
    onAttach: (Uri, String) -> Unit,
    onRemoveFile: (StudentFile) -> Unit,
    /** Non-null only for a teacher previewing the student view. */
    onTeacherPanel: (() -> Unit)? = null,
) {
    val extras = LocalChronosColors.current
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        val msg = state.error ?: return@LaunchedEffect
        if (state.classes.isEmpty()) return@LaunchedEffect  // the retry screen shows it
        snackbar.showSnackbar(msg)
        onDismissError()
    }

    if (state.booting) {
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background), Alignment.Center) {
            CircularProgressIndicator(color = extras.crimsonFill, strokeWidth = 2.dp)
        }
        return
    }

    if (state.classes.isEmpty() && state.error != null) {
        Column(
            Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(state.error, color = MaterialTheme.colorScheme.onBackground, textAlign = TextAlign.Center)
            Spacer(Modifier.height(16.dp))
            Button(onClick = onRetry) { Text("Try again") }
            TextButton(onClick = onSignOut) { Text("Sign out") }
        }
        return
    }

    if (state.needsJoin) {
        JoinGate(
            isTeacher = state.isTeacher,
            error = state.joinError,
            busy = state.joining,
            onJoin = onJoin,
            onSignOut = onSignOut,
            onTeacherPanel = onTeacherPanel,
        )
        return
    }

    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            StudentDrawer(
                state = state,
                onSelectChat = { scope.launch { drawer.close() }; onSelectChat(it) },
                onDeleteChat = onDeleteChat,
                onNewChat = { scope.launch { drawer.close() }; onNewChat() },
                onSwitchClass = onSwitchClass,
                onSignOut = onSignOut,
                onTeacherPanel = onTeacherPanel?.let { back -> { scope.launch { drawer.close() }; back() } },
            )
        },
    ) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            snackbarHost = { SnackbarHost(snackbar) },
            topBar = {
                ChatTopBar(
                    title = state.chats.firstOrNull { it.id == state.activeChatId }?.title ?: "New chat",
                    className = state.activeClassName.orEmpty(),
                    status = state.status,
                    onMenu = { scope.launch { drawer.open() } },
                    onNewChat = onNewChat,
                )
            },
        ) { padding ->
            Column(
                Modifier
                    .padding(padding)
                    .fillMaxSize()
                    .imePadding()
            ) {
                MessageList(
                    messages = state.messages,
                    className = state.activeClassName,
                    chatId = state.activeChatId,
                    onStarter = { onInput(it); onSend() },
                    onStopTool = onStopTool,
                    onSound = onSound,
                    onReview = onReview,
                    modifier = Modifier.weight(1f),
                )
                Composer(
                    value = state.input,
                    enabled = !state.busy,
                    canSend = state.canSend,
                    toolkits = state.toolkits,
                    files = state.files,
                    filesMax = state.filesMax,
                    attaching = state.attaching,
                    onChange = onInput,
                    onSend = onSend,
                    onTool = onTool,
                    onAttach = onAttach,
                    onRemoveFile = onRemoveFile,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    title: String,
    className: String,
    status: ServerStatus,
    onMenu: () -> Unit,
    onNewChat: () -> Unit,
) {
    val extras = LocalChronosColors.current
    TopAppBar(
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        ),
        navigationIcon = {
            IconButton(onClick = onMenu) { Sym("menu", tint = MaterialTheme.colorScheme.onSurface) }
        },
        title = {
            Column {
                Text(
                    text = title,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier
                            .size(7.dp)
                            .background(
                                when (status) {
                                    ServerStatus.ONLINE -> extras.gold
                                    ServerStatus.WAKING -> extras.gold
                                    ServerStatus.OFFLINE -> MaterialTheme.colorScheme.error
                                }
                            )
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = when (status) {
                            ServerStatus.ONLINE -> className
                            ServerStatus.WAKING -> "Waking server…"
                            ServerStatus.OFFLINE -> "Offline"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = extras.muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        },
        actions = {
            IconButton(onClick = onNewChat) {
                Sym("edit_square", tint = MaterialTheme.colorScheme.onSurface)
            }
        },
    )
}

@Composable
private fun MessageList(
    messages: List<Message>,
    className: String?,
    chatId: String?,
    onStarter: (String) -> Unit,
    onStopTool: () -> Unit,
    onSound: (Sounds.Kind) -> Unit,
    onReview: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()

    // Follow the newest text as it streams in.
    LaunchedEffect(messages.size, messages.lastOrNull()?.content?.length) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }

    if (messages.isEmpty()) {
        EmptyThread(className, onStarter, modifier)
        return
    }

    LazyColumn(
        state = listState,
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        // Index keys keep each activity's saved progress attached to it while scrolled away.
        items(messages.size, key = { "$chatId:$it" }) { i ->
            val m = messages[i]
            when {
                m.toolStatus != null -> ToolStatusLine(m.toolStatus, onStopTool)
                m.tool != null -> LearningToolCard(m.tool, onSound, onReview)
                else -> MessageBubble(m)
            }
        }
    }
}

@Composable
private fun EmptyThread(className: String?, onStarter: (String) -> Unit, modifier: Modifier) {
    val extras = LocalChronosColors.current
    Column(
        modifier
            .fillMaxSize()
            .padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Ask anything from ${className ?: "this course"}",
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.onBackground,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Answers come from your course material, with the source attached.",
            style = MaterialTheme.typography.bodyMedium,
            color = extras.muted,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(26.dp))
        STARTERS.forEach { s ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(bottom = 10.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .border(1.dp, extras.rule)
                    .clickable { onStarter(s) }
                    .padding(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Sym("arrow_forward", size = 17.sp, tint = extras.crimsonFill)
                Spacer(Modifier.width(10.dp))
                Text(
                    text = s,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}

@Composable
private fun MessageBubble(m: Message) {
    val extras = LocalChronosColors.current

    if (m.role == Message.Role.STUDENT) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Box(
                Modifier
                    .fillMaxWidth(0.78f)
                    .background(extras.crimsonFill)
                    .padding(horizontal = 16.dp, vertical = 12.dp)
            ) {
                // Never markdown — the student's own text is shown verbatim.
                Text(
                    text = m.content,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            }
        }
        return
    }

    Column(Modifier.fillMaxWidth()) {
        if (m.content.isBlank() && m.streaming) {
            Text(
                text = "Thinking…",
                style = MaterialTheme.typography.titleMedium,
                color = extras.muted,
            )
        } else {
            MarkdownText(
                source = m.content,
                modifier = if (m.blocked) Modifier.padding(start = 14.dp) else Modifier,
            )
        }

        // Exactly one of blocked / gap shows; reviewed is independent and can
        // appear alongside. Order matches buildMsgEl (web/student.html:660-695).
        when {
            m.blocked -> Note("This message was flagged and wasn't answered.", extras.muted)
            m.gap -> Note("Sent to your teacher as a gap in the course material.", extras.crimsonFill)
        }
        if (m.reviewed) {
            Note("Based on the work you attached — your course material doesn't cover this.", extras.muted)
        }
        m.errorNote?.let { Note(it, MaterialTheme.colorScheme.error) }

        if (m.sources.isNotEmpty()) SourcesDisclosure(m.sources)
    }
}

@Composable
private fun Note(text: String, color: Color) {
    Spacer(Modifier.height(10.dp))
    Text(text = text, style = MaterialTheme.typography.bodyMedium, color = color)
}

/** Teacher-only: the API sends empty arrays to students, so this never appears for them. */
@Composable
private fun SourcesDisclosure(sources: List<String>) {
    val extras = LocalChronosColors.current
    var open by remember { mutableStateOf(false) }
    Spacer(Modifier.height(12.dp))
    Column(Modifier.fillMaxWidth().border(1.dp, extras.gold)) {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable { open = !open }
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Sym(if (open) "expand_less" else "expand_more", size = 18.sp, tint = extras.gold)
            Spacer(Modifier.width(8.dp))
            Text(
                text = "Sources used (${sources.size})",
                style = MaterialTheme.typography.labelSmall,
                color = extras.gold,
            )
        }
        if (open) {
            Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp)) {
                sources.forEach {
                    Text(
                        text = it,
                        style = MaterialTheme.typography.bodyMedium,
                        color = extras.muted,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Composer(
    value: String,
    enabled: Boolean,
    canSend: Boolean,
    toolkits: List<String>,
    files: List<StudentFile>,
    filesMax: Int,
    attaching: Boolean,
    onChange: (String) -> Unit,
    onSend: () -> Unit,
    onTool: (String) -> Unit,
    onAttach: (Uri, String) -> Unit,
    onRemoveFile: (StudentFile) -> Unit,
) {
    val extras = LocalChronosColors.current
    var toolMenu by remember { mutableStateOf(false) }
    var fileMenu by remember { mutableStateOf(false) }
    var pendingKind by remember { mutableStateOf("assignment") }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) onAttach(uri, pendingKind)
    }
    val full = filesMax > 0 && files.size >= filesMax

    if (files.isNotEmpty() || attaching) {
        FlowRow(
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            files.forEach { f ->
                Row(Modifier.border(1.dp, extras.rule).padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Sym(if (f.kind == "rubric") "checklist" else "assignment", size = 15.sp, tint = extras.crimsonFill)
                    Spacer(Modifier.width(6.dp))
                    Text(f.name, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 180.dp))
                    IconButton(onClick = { onRemoveFile(f) }, modifier = Modifier.size(30.dp)) {
                        Sym("close", size = 14.sp, tint = extras.muted)
                    }
                }
            }
            if (attaching) Text("Attaching…", style = MaterialTheme.typography.labelSmall, color = extras.muted,
                modifier = Modifier.padding(6.dp))
        }
    }

    Row(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, extras.rule)
            .padding(10.dp)
            .navigationBarsPadding(),
        verticalAlignment = Alignment.Bottom,
    ) {
        // Only the student's own work and its rubric; the server rejects course material.
        Box {
            IconButton(onClick = { fileMenu = true }, enabled = enabled && !attaching, modifier = Modifier.size(52.dp)) {
                Sym("add", size = 22.sp, tint = extras.muted)
            }
            DropdownMenu(expanded = fileMenu, onDismissRequest = { fileMenu = false }) {
                if (full) {
                    DropdownMenuItem(text = { Text("You can attach $filesMax files per chat") }, onClick = { fileMenu = false }, enabled = false)
                } else listOf("assignment" to "Add assignment", "rubric" to "Add rubric").forEach { (kind, label) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = { fileMenu = false; pendingKind = kind; pick.launch(DOC_MIME_TYPES) },
                    )
                }
            }
        }
        // Only the activity types this course has switched on, as the web's ✨ menu.
        if (toolkits.isNotEmpty()) {
            Box {
                IconButton(onClick = { toolMenu = true }, enabled = enabled, modifier = Modifier.size(52.dp)) {
                    Sym("auto_awesome", size = 22.sp, tint = extras.crimsonFill)
                }
                DropdownMenu(expanded = toolMenu, onDismissRequest = { toolMenu = false }) {
                    toolkits.forEach { kind ->
                        DropdownMenuItem(
                            text = { Text(toolLabel(kind)) },
                            onClick = { toolMenu = false; onTool(kind) },
                        )
                    }
                }
            }
        }
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            enabled = enabled,
            modifier = Modifier.weight(1f),
            placeholder = {
                Text("Ask about your course material", style = MaterialTheme.typography.bodyMedium)
            },
            maxLines = 5,
            shape = RectangleShape,
            keyboardOptions = KeyboardOptions.Default,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = extras.crimsonFill,
                unfocusedBorderColor = extras.rule,
            ),
        )
        Spacer(Modifier.width(8.dp))
        Button(
            onClick = onSend,
            enabled = canSend,
            shape = RectangleShape,
            contentPadding = PaddingValues(0.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = extras.crimsonFill,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            ),
            modifier = Modifier.size(52.dp),
        ) {
            if (!enabled) {
                CircularProgressIndicator(
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(18.dp),
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Sym("arrow_upward", size = 20.sp, tint = MaterialTheme.colorScheme.onPrimary)
            }
        }
    }
}
