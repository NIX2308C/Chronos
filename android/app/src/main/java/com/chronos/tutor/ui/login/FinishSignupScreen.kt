package com.chronos.tutor.ui.login

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.AuthRepository
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow
import kotlinx.coroutines.launch

/**
 * Recovery for an account that exists in Firebase but has no role on the
 * backend — the state app.py reports as 403 "Finish creating your account
 * first."
 *
 * This screen is why that state is not a dead end. Without it the account
 * cannot be signed into and cannot be recreated, so the email is permanently
 * unusable. The web reaches the same repair through resumeSignup().
 */
@Composable
fun FinishSignupScreen(
    auth: AuthRepository,
    onDone: () -> Unit,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    val scope = rememberCoroutineScope()

    var role by remember { mutableStateOf("student") }
    var teacherCode by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "ONE STEP LEFT",
                style = MaterialTheme.typography.labelSmall,
                color = extras.muted,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = "Finish creating your account",
                style = MaterialTheme.typography.headlineLarge,
                color = MaterialTheme.colorScheme.onBackground,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "Your sign-up did not quite complete. Pick how you use Chronos and it is done.",
                style = MaterialTheme.typography.bodyMedium,
                color = extras.muted,
                textAlign = TextAlign.Center,
            )

            Spacer(Modifier.height(28.dp))

            Column(
                modifier = Modifier
                    .widthIn(max = 420.dp)
                    .fillMaxWidth()
                    .hardShadow(extras.crimsonFill, 7.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .border(1.dp, extras.rule)
                    .padding(22.dp),
            ) {
                Text(
                    text = "I AM A",
                    style = MaterialTheme.typography.labelSmall,
                    color = extras.muted,
                )
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    listOf("student" to "Student", "teacher" to "Teacher").forEach { (value, label) ->
                        val active = role == value
                        OutlinedButton(
                            onClick = { role = value },
                            enabled = !busy,
                            shape = RectangleShape,
                            border = BorderStroke(1.dp, if (active) extras.crimsonFill else extras.rule),
                            colors = ButtonDefaults.outlinedButtonColors(
                                containerColor = if (active) extras.crimsonFill else Color.Transparent,
                                contentColor = if (active) MaterialTheme.colorScheme.onPrimary
                                else MaterialTheme.colorScheme.onSurface,
                            ),
                            modifier = Modifier.weight(1f),
                        ) {
                            Text(text = label, style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }

                AnimatedVisibility(visible = role == "teacher") {
                    Column {
                        Spacer(Modifier.height(16.dp))
                        OutlinedTextField(
                            value = teacherCode,
                            onValueChange = { teacherCode = it },
                            enabled = !busy,
                            singleLine = true,
                            label = { Text("Teacher code") },
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            shape = RectangleShape,
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor = extras.crimsonFill,
                                unfocusedBorderColor = extras.rule,
                                focusedLabelColor = extras.crimsonFill,
                            ),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }

                AnimatedVisibility(visible = error != null) {
                    Column {
                        Spacer(Modifier.height(14.dp))
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(MaterialTheme.colorScheme.errorContainer)
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Sym("error", size = 18.sp, tint = MaterialTheme.colorScheme.onErrorContainer)
                            Spacer(Modifier.width(8.dp))
                            Text(
                                text = error.orEmpty(),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(20.dp))

                Button(
                    onClick = {
                        if (busy) return@Button
                        busy = true
                        error = null
                        scope.launch {
                            runCatching {
                                auth.finishSignup(role, teacherCode.takeIf { role == "teacher" })
                            }.fold(
                                onSuccess = { busy = false; onDone() },
                                onFailure = { busy = false; error = it.message ?: "Something went wrong." },
                            )
                        }
                    },
                    enabled = !busy,
                    shape = RectangleShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = extras.crimsonFill,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(52.dp),
                ) {
                    if (busy) {
                        CircularProgressIndicator(
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(18.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text("FINISH", style = MaterialTheme.typography.labelLarge)
                    }
                }

                Spacer(Modifier.height(4.dp))

                TextButton(
                    onClick = onSignOut,
                    enabled = !busy,
                    shape = RectangleShape,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "Use a different account",
                        style = MaterialTheme.typography.labelLarge,
                        color = extras.muted,
                    )
                }
            }
        }
    }
}
