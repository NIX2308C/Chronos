package com.chronos.tutor.ui.nav

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.chronos.tutor.AppContainer
import com.chronos.tutor.data.AuthState
import com.chronos.tutor.ui.RootViewModel
import com.chronos.tutor.ui.login.FinishSignupScreen
import com.chronos.tutor.ui.login.LoginScreen
import com.chronos.tutor.ui.login.LoginUiState
import com.chronos.tutor.ui.login.LoginViewModel
import com.chronos.tutor.ui.student.ChatScreen
import com.chronos.tutor.ui.student.ChatViewModel

/**
 * The whole navigation graph.
 *
 * The start destination is driven by [AuthState] rather than being hardcoded,
 * so a returning user never sees the login screen flash before being sent on.
 * Only routes that are fully built appear here — nothing links to a screen that
 * does not exist yet.
 */
@Composable
fun ChronosNav(container: AppContainer, root: RootViewModel) {
    val navController = rememberNavController()
    val state by root.state.collectAsStateWithLifecycle()

    val start = when (state) {
        is AuthState.Ready -> Routes.HOME
        AuthState.NeedsRole -> Routes.FINISH_SIGNUP
        else -> Routes.LOGIN
    }

    // Follow auth changes that happen after launch (sign out, token death).
    LaunchedEffect(state) {
        val target = when (state) {
            is AuthState.Ready -> Routes.HOME
            AuthState.NeedsRole -> Routes.FINISH_SIGNUP
            AuthState.SignedOut -> Routes.LOGIN
            is AuthState.Unconfigured -> Routes.LOGIN
            AuthState.Loading -> null
        } ?: return@LaunchedEffect

        if (navController.currentDestination?.route != target) {
            navController.navigate(target) {
                popUpTo(0) { inclusive = true }
                launchSingleTop = true
            }
        }
    }

    NavHost(navController = navController, startDestination = start) {

        composable(Routes.LOGIN) {
            val unconfigured = state as? AuthState.Unconfigured
            val repo = container.authRepository

            if (unconfigured != null || repo == null) {
                // Firebase is unavailable, so there is no repository to drive a
                // ViewModel with. The real login screen still renders — it just
                // carries the reason and cannot be submitted. Showing the actual
                // UI matters: a build that cannot sign in must still prove what
                // it is, rather than dying on launch the way the first one did.
                LoginScreen(
                    state = LoginUiState(
                        error = unconfigured?.message
                            ?: "Sign-in is not configured in this build.",
                    ),
                    onMode = {},
                    onEmail = {},
                    onPassword = {},
                    onToggleReveal = {},
                    onRole = {},
                    onTeacherCode = {},
                    onSubmit = {},
                )
                return@composable
            }

            // viewModel(), not remember(): a ViewModel created by remember is
            // never cleared, so its viewModelScope outlives the screen.
            val vm: LoginViewModel = viewModel(factory = object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T =
                    LoginViewModel(repo) as T
            })
            val ui by vm.state.collectAsStateWithLifecycle()
            LoginScreen(
                state = ui,
                onMode = vm::setMode,
                onEmail = vm::setEmail,
                onPassword = vm::setPassword,
                onToggleReveal = vm::toggleShowPassword,
                onRole = vm::setRole,
                onTeacherCode = vm::setTeacherCode,
                onSubmit = { vm.submit(onSuccess = root::onAuthenticated) },
            )
        }

        composable(Routes.FINISH_SIGNUP) {
            val repo = container.authRepository ?: return@composable
            FinishSignupScreen(
                auth = repo,
                onDone = root::onAuthenticated,
                onSignOut = root::signOut,
            )
        }

        composable(Routes.HOME) {
            val me = (state as? AuthState.Ready)?.me
            val vm: ChatViewModel = viewModel(
                // Keyed on uid so signing in as someone else does not inherit
                // the previous user's conversations.
                key = "chat-" + (me?.uid ?: "anon"),
                factory = object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        ChatViewModel(
                            chatRepo = container.chatRepository,
                            classRepo = container.classRepository,
                            prefs = container.prefs,
                            isTeacher = me?.role == "teacher",
                        ) as T
                },
            )
            val ui by vm.state.collectAsStateWithLifecycle()
            ChatScreen(
                state = ui,
                onInput = vm::setInput,
                onSend = vm::send,
                onSelectChat = { vm.selectChat(it) },
                onDeleteChat = { vm.deleteChat(it) },
                onNewChat = { vm.newChat() },
                onSwitchClass = { vm.switchClass(it) },
                onJoin = { vm.join(it) },
                onSignOut = root::signOut,
                onRetry = { vm.retry() },
                onDismissError = vm::dismissError,
            )
        }
    }
}
