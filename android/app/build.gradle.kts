plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

// versionCode = git commit count, so every commit yields a unique,
// monotonically increasing versionCode for F-Droid with no manual bumps.
// Falls back to 1 outside a git checkout.
val gitVersionCode: Int =
    providers.exec { commandLine("git", "rev-list", "--count", "HEAD") }
        .standardOutput.asText.get().trim().toIntOrNull() ?: 1

android {
    namespace = "dev.voicepipe.zwangli"
    compileSdk = 34

    defaultConfig {
        applicationId = "dev.voicepipe.zwangli"
        minSdk = 24
        targetSdk = 34
        versionCode = gitVersionCode
        versionName = "0.1.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildFeatures {
        buildConfig = true
    }

    val releaseSigning = run {
        val keystorePath = (project.findProperty("zwangliKeystorePath") as String?)
            ?: System.getenv("ZWANGLI_KEYSTORE_PATH")
        val storePass = (project.findProperty("zwangliKeystorePassword") as String?)
            ?: System.getenv("ZWANGLI_KEYSTORE_PASSWORD")
        val alias = (project.findProperty("zwangliKeyAlias") as String?)
            ?: System.getenv("ZWANGLI_KEY_ALIAS")
        val keyPass = (project.findProperty("zwangliKeyPassword") as String?)
            ?: System.getenv("ZWANGLI_KEY_PASSWORD")
        if (
            !keystorePath.isNullOrBlank() &&
            !storePass.isNullOrBlank() &&
            !alias.isNullOrBlank() &&
            !keyPass.isNullOrBlank() &&
            file(keystorePath).exists()
        ) {
            mapOf(
                "path" to keystorePath,
                "storePass" to storePass,
                "alias" to alias,
                "keyPass" to keyPass,
            )
        } else null
    }

    signingConfigs {
        releaseSigning?.let { conf ->
            create("release") {
                storeFile = file(conf.getValue("path"))
                storePassword = conf.getValue("storePass")
                keyAlias = conf.getValue("alias")
                keyPassword = conf.getValue("keyPass")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    sourceSets {
        named("main") {
            java.srcDirs("src/main/kotlin")
        }
        named("test") {
            java.srcDirs("src/test/kotlin")
        }
        named("debug") {
            java.srcDirs("src/debug/kotlin")
        }
        named("androidTest") {
            java.srcDirs("src/androidTest/kotlin")
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)

    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.okhttp.mockwebserver)
    androidTestImplementation(libs.kotlinx.coroutines.test)
}
