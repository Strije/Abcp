import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    id("kotlin-parcelize")
}

val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) {
        file.inputStream().use { load(it) }
    }
}

android {
    namespace = "com.example.myapplication"
    compileSdk = 36

    defaultConfig {
        applicationId = "ru.avtodrug92.app"
        minSdk = 24
        targetSdk = 36
        // Номер сборки CI = номер версии: каждая новая сборка «новее» прошлой, на этом держится автообновление
        val build = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
        versionCode = build
        versionName = "1.0.$build"

        // Наш сервер (server/): баланс, оплата, картинки. Тестовый — на VPS в NL, к выпуску переедет в РФ.
        buildConfigField("String", "SERVER_URL", "\"${localProperties.getProperty("SERVER_URL", "https://9077635-oy742028.twc1.net:8446")}\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }
    }

    // Постоянный debug-ключ: в CI путь приходит в DEBUG_KEYSTORE (из секрета),
    // иначе Gradle создаёт новый ключ на каждой сборке и APK не ставится поверх прошлого.
    signingConfigs {
        getByName("debug") {
            System.getenv("DEBUG_KEYSTORE")?.let { storeFile = file(it) }
        }
        // Боевой ключ (секреты RELEASE_KEYSTORE_B64 / RELEASE_KEYSTORE_PASS). Его же отпечаток — в RuStore.
        create("release") {
            System.getenv("RELEASE_KEYSTORE")?.let {
                storeFile = file(it)
                storePassword = System.getenv("RELEASE_KEYSTORE_PASS")
                keyAlias = "avtodrug92"
                keyPassword = System.getenv("RELEASE_KEYSTORE_PASS")
            }
        }
    }

    buildTypes {
        release {
            // Без боевого ключа (локальная сборка) — подписываем debug-ключом, чтобы сборка не падала
            signingConfig = signingConfigs.getByName(if (System.getenv("RELEASE_KEYSTORE") != null) "release" else "debug")
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    // Проверка lint не должна останавливать выпуск — предупреждения смотрим отдельно
    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.google.code.gson:gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("io.coil-kt:coil-compose:2.6.0")
    implementation("io.coil-kt:coil-gif:2.6.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.security:security-crypto:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    // Распознавание текста с фото (VIN, госномер) прямо на телефоне, без интернета и Google-сервисов
    implementation("com.google.mlkit:text-recognition:16.0.1")
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
