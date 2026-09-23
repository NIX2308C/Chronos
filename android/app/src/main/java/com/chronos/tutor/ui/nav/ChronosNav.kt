package com.chronos.tutor.ui.nav

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
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
import com.chronos.tutor.ui.common.MATERIAL_TOUR
import com.chronos.tutor.ui.common.STATS_TOUR
import com.chronos.tutor.ui.common.STUDENT_TOUR
import com.chronos.tutor.ui.common.TourHost
import com.chronos.tutor.ui.settings.SettingsScreen
import com.chronos.tutor.ui.settings.SettingsViewModel
import com.chronos.tutor.ui.settings.StatusScreen
import com.chronos.tutor.ui.teacher.TeacherScreen
import com.chronos.tutor.ui.teacher.TeacherSection
import com.chronos.tutor.ui.teacher.TeacherViewModel
import androidx.compose.runtime.rememberCoroutineScope
import androidx.navigation.NavType
import androidx.navigation.navArgument
import kotlinx.coroutines.launch

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

    fun homeFor(s: AuthState.Ready) = if (s.me.role == "teacher") Routes.TEACHER_HOME else Routes.HOME

    val start = when (val s = state) {
        is AuthState.Ready -> homeFor(s)
        AuthState.NeedsRole -> Routes.FINISH_SIGNUP
        else -> Routes.LOGIN
    }

    // Follow auth changes that happen after launch (sign out, token death).
    LaunchedEffect(state) {
        val target = when (val s = state) {
            is AuthState.Ready -> homeFor(s)
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

        composable(Routes.HOME) { entry ->
            val me = (state as? AuthState.Ready)?.me
            val context = LocalContext.current
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
                            sounds = container.sounds,
                            isDev = me?.isDev == true,
                        ) as T
                },
            )
            val ui by vm.state.collectAsStateWithLifecycle()
            // Settings → "Delete every conversation" reports back here.
            val cleared by entry.savedStateHandle.getStateFlow(CHATS_CLEARED, false).collectAsStateWithLifecycle()
            LaunchedEffect(cleared) {
                if (cleared) { entry.savedStateHandle[CHATS_CLEARED] = false; vm.reloadChats() }
            }
            val seen by container.prefs.tutorialSeenStudent.collectAsStateWithLifecycle(null)
            val scope = rememberCoroutineScope()
            TourHost(seen, STUDENT_TOUR, onSeen = { scope.launch { container.prefs.setTutorialSeenStudent(true) } }) { openTour ->
            ChatScreen(
                state = ui,
                onHelp = openTour,
                onSettings = { navController.navigate(Routes.settings(tutor = true)) },
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
                onTool = vm::invokeTool,
                onStopTool = vm::stopTool,
                onSound = vm::playSound,
                onReview = vm::sendText,
                onAttach = { uri, kind -> vm.attach(context.contentResolver, uri, kind) },
                onRemoveFile = { vm.removeFile(it) },
                onTeacherPanel = if (me?.role == "teacher") {
                    { navController.popBackStack(Routes.TEACHER_HOME, inclusive = false) }
                } else null,
                email = me?.email,
                onOpenJoin = vm::openJoin,
                onCloseJoin = vm::closeJoin,
            )
            }
        }

        composable(Routes.TEACHER_HOME) {
            val me = (state as? AuthState.Ready)?.me
            val vm: TeacherViewModel = viewModel(
                key = "teacher-" + (me?.uid ?: "anon"),
                factory = object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        TeacherViewModel(container.teacherRepository, container.classRepository, container.prefs) as T
                },
            )
            val ui by vm.state.collectAsStateWithLifecycle()
            val seenMaterial by container.prefs.tutorialSeenTeacher.collectAsStateWithLifecycle(null)
            val seenStats by container.prefs.tutorialSeenStats.collectAsStateWithLifecycle(null)
            val scope = rememberCoroutineScope()
            // Each teacher page has its own tour, as on the web.
            val analytics = ui.section == TeacherSection.ANALYTICS
            TourHost(
                seen = if (analytics) seenStats else seenMaterial,
                steps = if (analytics) STATS_TOUR else MATERIAL_TOUR,
                onSeen = {
                    scope.launch {
                        if (analytics) container.prefs.setTutorialSeenStats(true)
                        else container.prefs.setTutorialSeenTeacher(true)
                    }
                },
            ) { openTour ->
                TeacherScreen(
                    vm = vm,
                    state = ui,
                    onPreview = { navController.navigate(Routes.HOME) },
                    onSettings = { navController.navigate(Routes.settings(tutor = false)) },
                    onHelp = openTour,
                    onSignOut = root::signOut,
                )
            }
        }

        composable(
            Routes.SETTINGS,
            arguments = listOf(navArgument("tutor") { type = NavType.BoolType; defaultValue = false }),
        ) { entry ->
            val me = (state as? AuthState.Ready)?.me ?: return@composable
            // Captured now: by the time the delete finishes the user may have left.
            val caller = navController.previousBackStackEntry
            SettingsScreen(
                vm = settingsVm(container),
                me = me,
                tutor = entry.arguments?.getBoolean("tutor") == true,
                onBack = { navController.popBackStack() },
                onStatus = { navController.navigate(Routes.STATUS) },
                onSignOut = root::signOut,
                onChatsCleared = { caller?.savedStateHandle?.set(CHATS_CLEARED, true) },
            )
        }

        composable(Routes.STATUS) {
            StatusScreen(vm = settingsVm(container), onBack = { navController.popBackStack() })
        }
    }
}

private const val CHATS_CLEARED = "chatsCleared"

@Composable
private fun settingsVm(container: AppContainer): SettingsViewModel = viewModel(
    factory = object : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            SettingsViewModel(container.settingsRepository, container.chatRepository, container.prefs) as T
    },
)
