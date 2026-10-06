import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("rust")
    id("com.chaquo.python")
}

// Pure-Python wheels and exact pins prepared by `python3 scripts/dev.py android-python`
// (hash-checked against uv.lock): Chaquopy installs only these, offline.
val androidPython = rootProject.file("../../../dist/android-python")

val tauriProperties = Properties().apply {
    val propFile = file("tauri.properties")
    if (propFile.exists()) {
        propFile.inputStream().use { load(it) }
    }
}

android {
    compileSdk = 36
    namespace = "io.github.guillaumeboileaupro.controltv"
    defaultConfig {
        manifestPlaceholders["usesCleartextTraffic"] = "false"
        applicationId = "io.github.guillaumeboileaupro.controltv"
        minSdk = 24
        targetSdk = 36
        // The first Android target is arm64 phones only.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
        versionCode = tauriProperties.getProperty("tauri.android.versionCode", "1").toInt()
        versionName = tauriProperties.getProperty("tauri.android.versionName", "1.0")
    }
    buildTypes {
        getByName("debug") {
            manifestPlaceholders["usesCleartextTraffic"] = "true"
            isDebuggable = true
            isJniDebuggable = true
            isMinifyEnabled = false
            packaging {                jniLibs.keepDebugSymbols.add("*/arm64-v8a/*.so")
                jniLibs.keepDebugSymbols.add("*/armeabi-v7a/*.so")
                jniLibs.keepDebugSymbols.add("*/x86/*.so")
                jniLibs.keepDebugSymbols.add("*/x86_64/*.so")
            }
        }
        getByName("release") {
            isMinifyEnabled = true
            proguardFiles(
                *fileTree(".") { include("**/*.pro") }
                    .plus(getDefaultProguardFile("proguard-android-optimize.txt"))
                    .toList().toTypedArray()
            )
        }
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
    buildFeatures {
        buildConfig = true
    }
}

// Chaquopy's Python 3.12 exists for arm64-v8a and x86_64 only and configures every variant:
// keep the arm64 and (arm64-only, see gradle.properties) universal flavors, drop the others.
androidComponents {
    beforeVariants { variant ->
        if (variant.flavorName !in setOf("arm64", "universal")) {
            variant.enable = false
        }
    }
}

rust {
    // The repository root, where the Tauri CLI runs (`npx --prefix ui tauri`).
    rootDirRel = "../../../../"
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            options("--no-index", "--find-links", androidPython.resolve("wheels").absolutePath)
            install("-r", androidPython.resolve("requirements.txt").absolutePath)
        }
    }
}

dependencies {
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-process:2.10.0")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.4")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.0")
}

apply(from = "tauri.build.gradle.kts")