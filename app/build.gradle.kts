plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "ru.definitly.myauchan"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.definitly.myauchan"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }
}
