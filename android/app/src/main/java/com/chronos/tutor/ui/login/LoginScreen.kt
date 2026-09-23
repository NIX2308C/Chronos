package com.chronos.tutor.ui.login

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.keyframes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow
import kotlin.math.roundToInt

@Composable
fun LoginScreen(
    state: LoginUiState,
    onMode: (Boolean) -> Unit,
    onEmail: (String) -> Unit,
    onPassword: (String) -> Unit,
    onToggleReveal: () -> Unit,
    onRole: (String) -> Unit,
    onTeacherCode: (String) -> Unit,
    onSubmit: () -> Unit,
    /** From /health: null while checking. */
    online: Boolean? = null,
) {
    val extras = LocalChronosColors.current

    // Shake on failure, as the web card does. Keyed on the nonce so a second
    // identical error still re-runs the animation.
    val shake = remember { Animatable(0f) }
    LaunchedEffect(state.errorNonce) {
        if (state.errorNonce > 0) {
            shake.snapTo(0f)
            shake.animateTo(
                targetValue = 0f,
                animationSpec = keyframes {
                    durationMillis = 320
                    -9f at 60
                    8f at 120
                    -6f at 180
                    4f at 240
                    0f at 320
                },
            )
        }
    }

    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .padding(horizontal = 24.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "CHRONOS",
                style = MaterialTheme.typography.labelSmall,
                color = extras.muted,
            )
            Spacer(Modifier.height(6.dp))
            // Server reachability, as the web's status line (login.html:312).
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).background(if (online == false) extras.crimsonFill else extras.gold))
                Spacer(Modifier.width(7.dp))
                Text(
                    text = when (online) { null -> "Connecting…"; true -> "System online"; false -> "Server offline" },
                    style = MaterialTheme.typography.labelSmall,
                    color = extras.muted,
                )
            }
            Spacer(Modifier.height(8.dp))
            Text(
                text = if (state.signUp) "Create your account" else "Sign in",
                style = MaterialTheme.typography.headlineLarge,
                color = MaterialTheme.colorScheme.onBackground,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "Answers built on your course material, not the internet.",
                style = MaterialTheme.typography.bodyMedium,
                color = extras.muted,
                textAlign = TextAlign.Center,
            )

            Spacer(Modifier.height(28.dp))

            // The "case file" card: hard offset shadow, square corners, no elevation.
            Column(
                modifier = Modifier
                    .widthIn(max = 420.dp)
                    .fillMaxWidth()
                    .offset { IntOffset(shake.value.roundToInt(), 0) }
                    .hardShadow(extras.crimsonFill, 7.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .border(1.dp, extras.rule)
                    .padding(22.dp),
            ) {
                ModeTabs(signUp = state.signUp, onMode = onMode, enabled = !state.busy)

                Spacer(Modifier.height(18.dp))

                if (state.signUp) {
                    RolePicker(role = state.role, onRole = onRole, enabled = !state.busy)
                    Spacer(Modifier.height(16.dp))
                }

                Field(
                    value = state.email,
                    onValueChange = onEmail,
                    label = "Email",
                    enabled = !state.busy,
                    keyboardType = KeyboardType.Email,
                )
                Spacer(Modifier.height(12.dp))

                Field(
                    value = state.password,
                    onValueChange = onPassword,
                    label = "Password",
                    enabled = !state.busy,
                    keyboardType = KeyboardType.Password,
                    visual = if (state.showPassword) VisualTransformation.None
                    else PasswordVisualTransformation(),
                    trailing = {
                        TextButton(onClick = onToggleReveal) {
                            Sym(
                                name = if (state.showPassword) "visibility_off" else "visibility",
                                size = 20.sp,
                                tint = extras.muted,
                            )
                        }
                    },
                    imeAction = if (state.teacherCodeVisible) ImeAction.Next else ImeAction.Done,
                )

                AnimatedVisibility(visible = state.teacherCodeVisible) {
                    Column {
                        Spacer(Modifier.height(12.dp))
                        Field(
                            value = state.teacherCode,
                            onValueChange = onTeacherCode,
                            label = "Teacher code",
                            enabled = !state.busy,
                            keyboardType = KeyboardType.Password,
                            visual = PasswordVisualTransformation(),
                            imeAction = ImeAction.Done,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = "Ask whoever set up Chronos for your school.",
                            style = MaterialTheme.typography.labelSmall,
                            color = extras.muted,
                        )
                    }
                }

                AnimatedVisibility(visible = state.error != null) {
                    Column {
                        Spacer(Modifier.height(14.dp))
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(MaterialTheme.colorScheme.errorContainer)
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Sym(
                                name = "error",
                                size = 18.sp,
                                tint = MaterialTheme.colorScheme.onErrorContainer,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                text = state.error.orEmpty(),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(20.dp))

                Button(
                    onClick = onSubmit,
                    enabled = state.canSubmit,
                    shape = RectangleShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = extras.crimsonFill,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(52.dp),
                ) {
                    if (state.busy) {
                        CircularProgressIndicator(
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(18.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text(
                            text = if (state.signUp) "CREATE ACCOUNT" else "SIGN IN",
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ModeTabs(signUp: Boolean, onMode: (Boolean) -> Unit, enabled: Boolean) {
    val extras = LocalChronosColors.current
    Row(Modifier.fillMaxWidth()) {
        listOf(false to "SIGN IN", true to "SIGN UP").forEach { (isSignUp, label) ->
            val active = signUp == isSignUp
            TextButton(
                onClick = { onMode(isSignUp) },
                enabled = enabled,
                shape = RectangleShape,
                colors = ButtonDefaults.textButtonColors(
                    containerColor = if (active) extras.crimsonFill
                    else MaterialTheme.colorScheme.surfaceContainerHigh,
                    contentColor = if (active) MaterialTheme.colorScheme.onPrimary
                    else MaterialTheme.colorScheme.onSurface,
                ),
                modifier = Modifier
                    .weight(1f)
                    .height(42.dp),
            ) {
                Text(text = label, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun RolePicker(role: String, onRole: (String) -> Unit, enabled: Boolean) {
    val extras = LocalChronosColors.current
    Column {
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
                    onClick = { onRole(value) },
                    enabled = enabled,
                    shape = RectangleShape,
                    border = BorderStroke(1.dp, if (active) extras.crimsonFill else extras.rule),
                    colors = ButtonDefaults.outlinedButtonColors(
                        containerColor = if (active) extras.crimsonFill else Color.Transparent,
                        contentColor = if (active) MaterialTheme.colorScheme.onPrimary
                        else MaterialTheme.colorScheme.onSurface,
                    ),
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }
    }
}

@Composable
private fun Field(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    enabled: Boolean,
    keyboardType: KeyboardType = KeyboardType.Text,
    visual: VisualTransformation = VisualTransformation.None,
    trailing: @Composable (() -> Unit)? = null,
    imeAction: ImeAction = ImeAction.Next,
) {
    val extras = LocalChronosColors.current
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        singleLine = true,
        label = { Text(text = label, style = MaterialTheme.typography.labelLarge) },
        visualTransformation = visual,
        trailingIcon = trailing,
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType, imeAction = imeAction),
        shape = RectangleShape,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = extras.crimsonFill,
            unfocusedBorderColor = extras.rule,
            focusedLabelColor = extras.crimsonFill,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}
