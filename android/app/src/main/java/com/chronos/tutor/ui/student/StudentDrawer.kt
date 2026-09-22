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
    onTeacherPanel: (() -> Unit)? = null,
) {
    val extras = LocalChronosColors.current

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
                CourseSwitcher(state, onSwitchClass)
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

            LazyColumn(Modifier.weight(1f)) {
                items(state.chats.size) { i ->
                    val chat = state.chats[i]
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
                        IconButton(onClick = { onDeleteChat(chat.id) }) {
                            // Always visible: on touch there is no hover to reveal it.
                            Sym("delete", size = 17.sp, tint = extras.muted)
                        }
                    }
                }
            }

            HorizontalDivider(color = extras.rule)
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
