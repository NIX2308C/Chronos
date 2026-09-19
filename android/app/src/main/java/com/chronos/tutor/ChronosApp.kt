package com.chronos.tutor

import android.app.Application
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions

/**
 * Firebase is initialised by hand rather than by google-services.json and the
 * gms plugin.
 *
 * This app uses Firebase for email/password auth only — no Google Sign-In, no
 * Analytics, no Firestore (firestore.rules denies all client access, so every
 * read and write goes through app.py). Email/password needs exactly the three
 * values below, and the Web API key is already public: app.py serves it
 * unauthenticated at /auth/config. Doing it this way also means the app works
 * on first launch without a network round-trip for its own config.
 */
class ChronosApp : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()

        require(BuildConfig.FB_APP_ID.isNotBlank()) {
            "fbAppId is not set. Add it to android/gradle.properties (or pass " +
                "-PfbAppId=...). Get it from the Firebase console: Project settings > " +
                "Your apps > Add app > Android, package name com.chronos.tutor. " +
                "No SHA-1 is needed for email/password auth."
        }

        FirebaseApp.initializeApp(
            this,
            FirebaseOptions.Builder()
                .setApiKey(BuildConfig.FB_API_KEY)
                .setProjectId(BuildConfig.FB_PROJECT)
                .setApplicationId(BuildConfig.FB_APP_ID)
                .build(),
        )

        container = AppContainer(this)
    }
}
