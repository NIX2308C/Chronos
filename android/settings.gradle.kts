pluginManagement {
    repositories {
        // google() stays first: the Android Gradle Plugin and the AndroidX and
        // Firebase artifacts are published to Google's Maven only.
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "Chronos"
include(":app")
