package com.chronos.tutor.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.chronos.tutor.ui.common.Sym
import com.chronos.tutor.ui.theme.LocalChronosColors

/** Service keys from /status/public, labelled as web/status.html does. */
private val SERVICE_LABELS = mapOf(
    "chronos" to "Chronos", "ai" to "AI service", "data" to "Learning data", "materials" to "Course materials",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatusScreen(vm: SettingsViewModel, onBack: () -> Unit) {
    val extras = LocalChronosColors.current
    val ui by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.watchStatus() }

    fun colorFor(s: String) = when (s) {
        "operational" -> extras.gold
        "degraded" -> Color(0xFFD93F3F)
        else -> extras.muted
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
                navigationIcon = { IconButton(onClick = onBack) { Sym("arrow_back", tint = MaterialTheme.colorScheme.onSurface) } },
                title = { Text("System status", style = MaterialTheme.typography.labelLarge) },
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val s = ui.status
            if (s == null) {
                Text(ui.statusError ?: "Checking…", color = extras.muted)
                return@Column
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(12.dp).background(colorFor(s.overall)))
                Spacer(Modifier.width(10.dp))
                Text(
                    if (s.overall == "degraded") "Some services are degraded" else "All services are operational",
                    style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
                )
            }
            Text(
                if (s.overall == "degraded") "Chronos may be slower or unavailable for some requests. We are checking it."
                else "Chronos is operating normally.",
                color = extras.muted, style = MaterialTheme.typography.bodyMedium,
            )
            s.services.forEach { (key, state) ->
                Row(
                    Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerLow).border(1.dp, extras.rule).padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(SERVICE_LABELS[key] ?: key, Modifier.weight(1f))
                    Box(Modifier.size(8.dp).background(colorFor(state)))
                    Spacer(Modifier.width(8.dp))
                    Text(
                        when (state) { "operational" -> "Operational"; "degraded" -> "Degraded"; else -> "Checking" },
                        color = colorFor(state), style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            s.checkedAt?.let { Text("Checked $it", style = MaterialTheme.typography.labelSmall, color = extras.muted) }
            ui.statusError?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error) }
        }
    }
}
