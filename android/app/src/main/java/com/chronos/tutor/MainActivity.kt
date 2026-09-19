package com.chronos.tutor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.chronos.tutor.data.AuthState
import com.chronos.tutor.ui.RootViewModel
import com.chronos.tutor.ui.nav.ChronosNav
import com.chronos.tutor.ui.theme.ChronosTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val root: RootViewModel by viewModels {
        val container = (application as ChronosApp).container
        object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                RootViewModel(container.authRepository, container.signedOutEvents) as T
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Hold the splash until we know where to send the user — otherwise a
        // returning user sees the login screen for a frame before being
        // redirected. Read from a plain field updated by the collector below,
        // never from Compose state: setKeepOnScreenCondition runs per frame,
        // outside any composition.
        var resolved = false
        splash.setKeepOnScreenCondition { !resolved }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                root.state.collect { if (it != AuthState.Loading) resolved = true }
            }
        }

        val container = (application as ChronosApp).container
        setContent {
            ChronosTheme {
                ChronosNav(container = container, root = root)
            }
        }
    }
}
