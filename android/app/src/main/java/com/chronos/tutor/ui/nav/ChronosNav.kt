package com.chronos.tutor.ui.nav

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.chronos.tutor.AppContainer
import com.chronos.tutor.data.AuthState
import com.chronos.tutor.ui.RootViewModel
import com.chronos.tutor.ui.account.AccountScreen
import com.chronos.tutor.ui.login.FinishSignupScreen
import com.chronos.tutor.ui.login.LoginScreen
import com.chronos.tutor.ui.login.LoginViewModel

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
            val vm = remember { LoginViewModel(container.authRepository) }
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
            FinishSignupScreen(
                auth = container.authRepository,
                onDone = root::onAuthenticated,
                onSignOut = root::signOut,
            )
        }

        // P0 ships the account screen here. P1 replaces this route with the
        // tutor chat; until then this is a complete small screen, not a stub.
        composable(Routes.HOME) {
            AccountScreen(
                state = state,
                api = container.api,
                onSignOut = root::signOut,
            )
        }
    }
}
