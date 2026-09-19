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
// the release config simply isn't created, and the build falls back to debug
// rather than silently emitting an unsigned APK nobody can install.
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
        versionCode = 1
        versionName = "1.0"

        buildConfigField("String", "API_BASE",    "\"https://$chronosHost\"")
        buildConfigField("String", "FB_API_KEY",  "\"$fbApiKey\"")
        buildConfigField("String", "FB_PROJECT",  "\"$fbProjectId\"")
        buildConfigField("String", "FB_APP_ID",   "\"$fbAppId\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (canSignRelease) {
                signingConfig = signingConfigs.getByName("release")
            }
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

    androidTestImplementation(libs.androidx.test.junit)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test)
    debugImplementation(libs.compose.ui.manifest)
}
