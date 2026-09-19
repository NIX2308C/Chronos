// Versions live in gradle/libs.versions.toml. See the comment there for the
// Gradle/AGP/Kotlin compatibility triple before bumping any of them.
plugins {
    alias(libs.plugins.android.application)  apply false
    alias(libs.plugins.kotlin.android)       apply false
    alias(libs.plugins.kotlin.compose)       apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
