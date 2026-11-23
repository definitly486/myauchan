plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releaseStoreFile = System.getenv("MYAUCHAN_STORE_FILE")
val releaseStorePassword = System.getenv("MYAUCHAN_STORE_PASSWORD")
val releaseKeyAlias = System.getenv("MYAUCHAN_KEY_ALIAS")
val releaseKeyPassword = System.getenv("MYAUCHAN_KEY_PASSWORD")
val hasReleaseSigning = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword
).all { !it.isNullOrBlank() }

android {
    namespace = "ru.definitly.myauchan"
    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    defaultConfig {
        // Separate package: installs alongside the original MyAuchan app.
        applicationId = "ru.definitly.myauchan.standalone"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.1"
    }

    if (hasReleaseSigning) {
        signingConfigs {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
        buildTypes {
            getByName("release") {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }
}
