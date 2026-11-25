// NOT BUILT. Written for a standard Android library module; the versions are a starting point for a developer to align with the app's own toolchain.
plugins {
    id("com.android.library") version "8.5.2"
    kotlin("android") version "1.9.24"
}

android {
    namespace = "com.zoiko.chipbridge"
    compileSdk = 34
    defaultConfig { minSdk = 23 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // Play Integrity Standard API for the capture's device report (CaptureAttestation.kt; ZID-CP-WF01 Q18)
    implementation("com.google.android.play:integrity:1.4.0")
}
