package com.chronos.tutor

import android.app.Application
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions

/**
 * Firebase is initialised by hand rather than by google-services.json and the
 * gms plugin.
 *
 * This app uses Firebase for email/password auth only — no Google Sign-In, no
 * Analytics, no Firestore (firestore.rules denies all client access, so every
 * read and write goes through app.py). Email/password needs exactly the three
 * values below, and the API key is already public: app.py serves one
 * unauthenticated at /auth/config. Doing it this way also means the app works
 * on first launch without a network round-trip for its own config.
 *
 * A bad or missing config must NOT stop the app from starting. An earlier
 * version threw from onCreate when fbAppId was blank, so the app died before
 * drawing a single pixel — indistinguishable from the app being broken, and
 * impossible to diagnose without adb. Now it starts, shows the real UI, and
 * reports the problem at the point where it actually matters: signing in.
 */
class ChronosApp : Application() {

    lateinit var container: AppContainer
        private set

    /** Null when Firebase could not be configured; auth is unavailable. */
    var configError: String? = null
        private set

    override fun onCreate() {
        super.onCreate()

        configError = initFirebase()
        container = AppContainer(this, firebaseReady = configError == null)
    }

    private fun initFirebase(): String? {
        if (BuildConfig.FB_APP_ID.isBlank() || BuildConfig.FB_API_KEY.isBlank()) {
            return "Sign-in is not configured in this build (missing Firebase app id or API key)."
        }
        return try {
            FirebaseApp.initializeApp(
                this,
                FirebaseOptions.Builder()
                    .setApiKey(BuildConfig.FB_API_KEY)
                    .setProjectId(BuildConfig.FB_PROJECT)
                    .setApplicationId(BuildConfig.FB_APP_ID)
                    .build(),
            )
            null
        } catch (e: Throwable) {
            Log.e("ChronosApp", "Firebase init failed", e)
            "Sign-in is unavailable: ${e.message ?: e::class.java.simpleName}"
        }
    }
}
