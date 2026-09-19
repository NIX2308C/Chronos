package com.chronos.tutor.ui.student

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow

/**
 * Shown when the account belongs to no course.
 *
 * Two variants, matching the web: a student joins with a code, while a teacher
 * is told to create a course on the web first — there is no course-creation UI
 * on mobile yet, so sending a teacher to a join-code box would be a dead end.
 */
@Composable
fun JoinGate(
    isTeacher: Boolean,
    error: String?,
    busy: Boolean,
    onJoin: (String) -> Unit,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    var code by remember { mutableStateOf("") }

    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(
            Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(
                Modifier
                    .widthIn(max = 420.dp)
                    .fillMaxWidth()
                    .hardShadow(extras.crimsonFill, 7.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .border(1.dp, extras.rule)
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Sym(if (isTeacher) "school" else "key", size = 30.sp, tint = extras.crimsonFill)
                Spacer(Modifier.height(14.dp))
                Text(
                    text = if (isTeacher) "Make a course first" else "Join your course",
                    style = MaterialTheme.typography.headlineMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = if (isTeacher)
                        "You haven't got any courses yet. Set one up in Course Material on the web, then come back and try the tutor on your own material."
                    else
                        "Enter the code your teacher gave you.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = extras.muted,
                    textAlign = TextAlign.Center,
                )

                if (!isTeacher) {
                    Spacer(Modifier.height(20.dp))
                    OutlinedTextField(
                        value = code,
                        onValueChange = { if (it.length <= 12) code = it.uppercase() },
                        enabled = !busy,
                        singleLine = true,
                        label = { Text("Course code") },
                        shape = RectangleShape,
                        keyboardOptions = KeyboardOptions(
                            capitalization = KeyboardCapitalization.Characters,
                        ),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = extras.crimsonFill,
                            unfocusedBorderColor = extras.rule,
                            focusedLabelColor = extras.crimsonFill,
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    )

                    AnimatedVisibility(visible = error != null) {
                        Column {
                            Spacer(Modifier.height(12.dp))
                            Text(
                                text = error.orEmpty(),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }

                    Spacer(Modifier.height(18.dp))
                    Button(
                        onClick = { onJoin(code) },
                        enabled = !busy && code.isNotBlank(),
                        shape = RectangleShape,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = extras.crimsonFill,
                            contentColor = MaterialTheme.colorScheme.onPrimary,
                        ),
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) {
                        if (busy) {
                            CircularProgressIndicator(
                                strokeWidth = 2.dp,
                                modifier = Modifier.size(18.dp),
                                color = MaterialTheme.colorScheme.onPrimary,
                            )
                        } else {
                            Text("JOIN", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }

                Spacer(Modifier.height(6.dp))
                TextButton(onClick = onSignOut, shape = RectangleShape, modifier = Modifier.fillMaxWidth()) {
                    Text("Sign out", style = MaterialTheme.typography.labelLarge, color = extras.muted)
                }
            }
        }
    }
}
