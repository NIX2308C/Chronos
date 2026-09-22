package com.chronos.tutor.ui.nav

/**
 * Every destination in the app.
 *
 * Drawers are not routes: they are ModalNavigationDrawer inside the screens
 * that own them, matching the web, where the drawer carries the course list
 * that every teacher screen needs.
 *
 * Routes not yet built are not listed and not reachable. Nothing in the app
 * links to a screen that does not exist yet — no greyed buttons, no "coming
 * soon" cards. Each build is a smaller complete app, never a partial big one.
 */
object Routes {
    const val LOGIN = "login"

    /** Roleless-account recovery. See FinishSignupScreen for why it exists. */
    const val FINISH_SIGNUP = "finish_signup"

    /**
     * Where a signed-in student lands: the tutor chat. Teachers reach it as
     * "Preview as student".
     */
    const val HOME = "home"

    /** Where a signed-in teacher lands: course material and analytics. */
    const val TEACHER_HOME = "teacher_home"

    /** `tutor=true` when opened from the chat: only then is the Tutor section shown, as on the web. */
    const val SETTINGS = "settings?tutor={tutor}"
    fun settings(tutor: Boolean) = "settings?tutor=$tutor"

    /** Public service status, from Settings → System status. */
    const val STATUS = "status"
}
