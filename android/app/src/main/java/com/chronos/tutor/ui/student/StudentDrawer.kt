package com.chronos.tutor.ui.student

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors

/**
 * The sidebar: course switcher, new chat, and this course's conversations.
 *
 * The chat list is already filtered to the active course by the ViewModel —
 * the web does the same, so a conversation from another course is never
 * reachable from here.
 */
@Composable
fun StudentDrawer(
    state: ChatUiState,
    onSelectChat: (String) -> Unit,
    onDeleteChat: (String) -> Unit,
    onNewChat: () -> Unit,
    onSwitchClass: (String) -> Unit,
    onSignOut: () -> Unit,
    onSettings: () -> Unit,
    onTeacherPanel: (() -> Unit)? = null,
    email: String? = null,
    onJoinAnother: (() -> Unit)? = null,
) {
    val extras = LocalChronosColors.current
    var filter by remember { mutableStateOf("") }
    // A filter box appears only with a long history (CHAT_FILTER_MIN, student.html:776).
    val filtering = state.chats.size >= 12
    val q = if (filtering) filter.trim().lowercase() else ""
    val shown = if (q.isEmpty()) state.chats
        else state.chats.filter { it.id == state.activeChatId || it.title.lowercase().contains(q) }
    var pendingDelete by remember { mutableStateOf<String?>(null) }

    pendingDelete?.let { id ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null }, shape = RectangleShape,
            title = { Text("Delete this conversation?") },
            confirmButton = {
                TextButton(onClick = { pendingDelete = null; onDeleteChat(id) }) {
                    Text("Delete", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { pendingDelete = null }) { Text("Cancel") } },
        )
    }

    ModalDrawerSheet(
        drawerShape = RectangleShape,
        drawerContainerColor = MaterialTheme.colorScheme.surfaceContainer,
        modifier = Modifier.width(300.dp),
    ) {
        Column(Modifier.fillMaxSize()) {
            Column(Modifier.padding(18.dp)) {
                Text(
                    text = "CHRONOS",
                    style = MaterialTheme.typography.labelSmall,
                    color = extras.muted,
                )
                Spacer(Modifier.height(14.dp))
                Row(Modifier.height(IntrinsicSize.Min)) {
                    Box(Modifier.weight(1f)) { CourseSwitcher(state, onSwitchClass) }
                    if (onJoinAnother != null) {
                        Spacer(Modifier.width(6.dp))
                        Box(
                            Modifier.width(44.dp).fillMaxHeight().border(1.dp, extras.rule).clickable { onJoinAnother() },
                            contentAlignment = Alignment.Center,
                        ) { Sym("add", size = 18.sp, tint = extras.muted) }
                    }
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = onNewChat,
                    shape = RectangleShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = extras.crimsonFill,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    ),
                    modifier = Modifier.fillMaxWidth().height(46.dp),
                ) {
                    Sym("add", size = 18.sp, tint = MaterialTheme.colorScheme.onPrimary)
                    Spacer(Modifier.width(8.dp))
                    Text("NEW CHAT", style = MaterialTheme.typography.labelLarge)
                }
            }

            HorizontalDivider(color = extras.rule)

            Text(
                text = "RECENT",
                style = MaterialTheme.typography.labelSmall,
                color = extras.muted,
                modifier = Modifier.padding(start = 18.dp, top = 16.dp, bottom = 8.dp),
            )
            if (filtering) {
                OutlinedTextField(
                    value = filter,
                    onValueChange = { filter = it },
                    singleLine = true,
                    placeholder = { Text("Filter conversations", style = MaterialTheme.typography.bodySmall) },
                    textStyle = MaterialTheme.typography.bodySmall,
                    shape = RectangleShape,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp).padding(bottom = 8.dp),
                )
            }
            if (q.isNotEmpty() && shown.isEmpty()) {
                Text("No matches", style = MaterialTheme.typography.bodySmall, color = extras.muted,
                    modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp))
            }

            LazyColumn(Modifier.weight(1f)) {
                items(shown.size) { i ->
                    val chat = shown[i]
                    val active = chat.id == state.activeChatId
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(if (active) extras.raised else Color.Transparent)
                            .clickable { onSelectChat(chat.id) }
                            .padding(vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        // Active row carries a crimson left rule, as on the web.
                        Box(
                            Modifier
                                .width(3.dp)
                                .height(22.dp)
                                .background(if (active) extras.crimsonFill else Color.Transparent)
                        )
                        Spacer(Modifier.width(13.dp))
                        Text(
                            text = chat.title,
                            style = MaterialTheme.typography.bodyMedium,
                            color = if (active) MaterialTheme.colorScheme.onSurface else extras.muted,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        IconButton(onClick = { pendingDelete = chat.id }) {
                            // Always visible: on touch there is no hover to reveal it.
                            Sym("delete", size = 17.sp, tint = extras.muted)
                        }
                    }
                }
            }

            HorizontalDivider(color = extras.rule)
            // Who is signed in, as the web's sidebar footer (student.html:520).
            Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(30.dp).background(MaterialTheme.colorScheme.onSurface), contentAlignment = Alignment.Center) {
                    Text((email ?: "?").take(2).uppercase(), style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.surface)
                }
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(email ?: "Account", style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(if (state.isTeacher) "Teacher" else "Student", style = MaterialTheme.typography.labelSmall, color = extras.muted)
                }
            }
            Row(
                Modifier.fillMaxWidth().clickable { onSettings() }.padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Sym("settings", size = 18.sp, tint = extras.muted)
                Spacer(Modifier.width(10.dp))
                Text("Settings", style = MaterialTheme.typography.bodyMedium, color = extras.muted)
            }
            if (onTeacherPanel != null) {
                Row(
                    Modifier.fillMaxWidth().clickable { onTeacherPanel() }.padding(18.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Sym("arrow_back", size = 18.sp, tint = extras.crimsonFill)
                    Spacer(Modifier.width(10.dp))
                    Text("Teacher panel", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
                }
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .clickable { onSignOut() }
                    .padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Sym("logout", size = 18.sp, tint = extras.muted)
                Spacer(Modifier.width(10.dp))
                Text(
                    text = "Sign out",
                    style = MaterialTheme.typography.bodyMedium,
                    color = extras.muted,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CourseSwitcher(state: ChatUiState, onSwitchClass: (String) -> Unit) {
    val extras = LocalChronosColors.current
    var open by remember { mutableStateOf(false) }

    Box {
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceContainerLow)
                .border(1.dp, extras.rule)
                .clickable(enabled = state.classes.size > 1) { open = true }
                .padding(horizontal = 12.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = state.activeClassName ?: "No course",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (state.classes.size > 1) {
                Sym("expand_more", size = 18.sp, tint = extras.muted)
            }
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            state.classes.forEach { c ->
                DropdownMenuItem(
                    text = { Text(c.name, style = MaterialTheme.typography.bodyMedium) },
                    onClick = { open = false; onSwitchClass(c.id) },
                )
            }
        }
    }
}
