package com.chronos.tutor.ui.account

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chronos.tutor.data.AuthState
import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ApiError
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors
import com.chronos.tutor.ui.theme.hardShadow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private sealed interface Health {
    data object Checking : Health
    data object Ok : Health
    data class Down(val reason: String) : Health
}

/**
 * P0's signed-in screen: who you are, what role the backend gave you, and
 * whether it is reachable.
 *
 * P1 replaces this route with the tutor chat. Until then it is deliberately a
 * complete small screen rather than a placeholder for one — there is nothing
 * here that hints at a feature that does not exist yet.
 */
@Composable
fun AccountScreen(
    state: AuthState,
    api: Api,
    onSignOut: () -> Unit,
) {
    val extras = LocalChronosColors.current
    val me = (state as? AuthState.Ready)?.me

    var health by remember { mutableStateOf<Health>(Health.Checking) }
    LaunchedEffect(Unit) {
        health = withContext(Dispatchers.IO) {
            runCatching { api.get("/health") }.fold(
                onSuccess = { Health.Ok },
                onFailure = {
                    Health.Down((it as? ApiError)?.userMessage ?: "Unreachable")
                },
            )
        }
    }

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
                text = "SIGNED IN",
                style = MaterialTheme.typography.labelSmall,
                color = extras.muted,
            )
            Spacer(Modifier.height(10.dp))

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
                    text = me?.email ?: "Unknown account",
                    style = MaterialTheme.typography.headlineMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(14.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier
                            .background(extras.crimsonFill)
                            .padding(horizontal = 10.dp, vertical = 5.dp)
                    ) {
                        Text(
                            text = (me?.role ?: "unknown").uppercase(),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    }
                }

                Spacer(Modifier.height(18.dp))
                HorizontalDivider(color = extras.rule)
                Spacer(Modifier.height(18.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    when (val h = health) {
                        Health.Checking -> {
                            CircularProgressIndicator(
                                strokeWidth = 2.dp,
                                modifier = Modifier.size(16.dp),
                                color = extras.muted,
                            )
                            Spacer(Modifier.width(10.dp))
                            Text(
                                text = "Checking the server",
                                style = MaterialTheme.typography.bodyMedium,
                                color = extras.muted,
                            )
                        }
                        Health.Ok -> {
                            Sym("check_circle", size = 18.sp, tint = extras.gold)
                            Spacer(Modifier.width(10.dp))
                            Text(
                                text = "Server reachable",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                        is Health.Down -> {
                            Sym("cloud_off", size = 18.sp, tint = MaterialTheme.colorScheme.error)
                            Spacer(Modifier.width(10.dp))
                            Text(
                                text = h.reason,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(22.dp))

                OutlinedButton(
                    onClick = onSignOut,
                    shape = RectangleShape,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(50.dp),
                ) {
                    Text("SIGN OUT", style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}
