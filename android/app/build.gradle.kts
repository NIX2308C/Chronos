plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// One source of truth for the backend host, so BuildConfig.API_BASE and any
// staging build can never drift. Overridden per build with -PchronosHost=<host>.
val chronosHost: String = (project.findProperty("chronosHost") ?: "chronos.tevproject.com") as String

// Firebase Auth needs only these three. There is deliberately no
// google-services.json and no gms plugin: this app uses Firebase for
// email/password auth only, and the Web API key is already public (app.py
// serves it unauthenticated at /auth/config). See the Auth section of the plan.
val fbApiKey: String    = (project.findProperty("fbApiKey")    ?: "") as String
val fbProjectId: String = (project.findProperty("fbProjectId") ?: "") as String
val fbAppId: String     = (project.findProperty("fbAppId")     ?: "") as String

// Release signing comes from the environment, never from a file in the repo —
// this is a public repository. CI writes the keystore to a temp path from a
// GitHub Actions secret and exports these four vars. With any of them missing
// the release config simply isn't created, and the release build is signed
// with the debug key rather than emitted unsigned where nobody can install it.
// It stays a release build either way: a debuggable, unminified APK runs
// Compose several times slower, which is what made the app feel sluggish.
val ksPath    = System.getenv("CHRONOS_KEYSTORE_PATH")
val ksPass    = System.getenv("CHRONOS_KEYSTORE_PASSWORD")
val ksAlias   = System.getenv("CHRONOS_KEY_ALIAS")
val ksKeyPass = System.getenv("CHRONOS_KEY_PASSWORD")
val canSignRelease = !ksPath.isNullOrBlank() && !ksPass.isNullOrBlank() &&
        !ksAlias.isNullOrBlank() && !ksKeyPass.isNullOrBlank() && file(ksPath).exists()

android {
    namespace = "com.chronos.tutor"
    compileSdk = 35

    signingConfigs {
        if (canSignRelease) {
            create("release") {
                storeFile = file(ksPath!!)
                storePassword = ksPass
                keyAlias = ksAlias
                keyPassword = ksKeyPass
            }
        }
    }

    defaultConfig {
        applicationId = "com.chronos.tutor"
        // 24, not the TWA's 21: Firebase needs 23+, and 24 also avoids
        // core-library desugaring for java.time. Costs ~1% of devices.
        minSdk = 24
        targetSdk = 35
        // The CI run number, which is also the release tag (android-vN), so
        // every published APK is a newer version Android will upgrade to.
        // Local builds are 1.
        versionCode = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
        versionName = "1.0.$versionCode"

        buildConfigField("String", "API_BASE",    "\"https://$chronosHost\"")
        buildConfigField("String", "FB_API_KEY",  "\"$fbApiKey\"")
        buildConfigField("String", "FB_PROJECT",  "\"$fbProjectId\"")
        buildConfigField("String", "FB_APP_ID",   "\"$fbAppId\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            // R8 is most of Compose's release-mode speed; shrinking resources
            // also keeps the APK small. Libraries bring their own keep rules.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName(if (canSignRelease) "release" else "debug")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions {
        jvmTarget = "11"
    }
}

dependencies {
    implementation(libs.core.ktx)
    implementation(libs.splashscreen)
    // Installs the baseline profiles Compose and friends ship, so a sideloaded
    // APK runs precompiled code from first launch instead of interpreting it.
    implementation(libs.profileinstaller)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.graphics)
    implementation(libs.compose.material3)
    implementation(libs.compose.tooling.prev)
    debugImplementation(libs.compose.tooling)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.vm.compose)
    implementation(libs.lifecycle.runtime)
    implementation(libs.lifecycle.runtime.ktx)
    implementation(libs.navigation.compose)
    implementation(libs.datastore.prefs)

    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    debugImplementation(libs.okhttp.logging)
    implementation(libs.serialization.json)

    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.auth)
    implementation(libs.coroutines.play)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.coroutines.test)

    androidTestImplementation(libs.androidx.test.junit)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test)
    debugImplementation(libs.compose.ui.manifest)
}
