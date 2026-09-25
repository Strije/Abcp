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
        // Только ARM — на телефонах в РФ других почти нет; x86 (эмуляторы) раздувал APK библиотеками распознавания
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }

        // Наш сервер (server/): баланс, оплата, картинки. Тестовый — на VPS в NL, к выпуску переедет в РФ.
        buildConfigField("String", "SERVER_URL", "\"${localProperties.getProperty("SERVER_URL", "https://9077635-oy742028.twc1.net:8446")}\"")

        // ID проекта push в Консоли RuStore — не секрет (он зашивается в любое приложение с push)
        // AppMetrica (Яндекс): вылеты и ошибки. Ключ приложения — не секрет. Пусто — ничего не отправляем.
        buildConfigField("String", "APPMETRICA_KEY", "\"${localProperties.getProperty("APPMETRICA_KEY", "717eaca7-de59-4828-aac1-11882fe2fe31")}\"")
        buildConfigField("String", "RUSTORE_PROJECT_ID", "\"ENyKTeW4scP40-pocP1gWiOTC-kB89sT\"")

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

    // Две версии из одного кода и с одной подписью (как у всех, кто в магазинах):
    // rustore — обновляет сам RuStore, без своего автообновления и без права ставить APK;
    // direct  — раздача напрямую (ссылка, сайт), обновляется с нашего сервера.
    flavorDimensions += "store"
    productFlavors {
        create("rustore") {
            dimension = "store"
            buildConfigField("Boolean", "SELF_UPDATE", "false")
        }
        create("direct") {
            dimension = "store"
            buildConfigField("Boolean", "SELF_UPDATE", "true")
        }
    }

    buildTypes {
        release {
            // Без боевого ключа (локальная сборка) — подписываем debug-ключом, чтобы сборка не падала
            signingConfig = signingConfigs.getByName(if (System.getenv("RELEASE_KEYSTORE") != null) "release" else "debug")
            // Сжатие: выкидываем неиспользуемый код и ресурсы библиотек (правила — proguard-rules.pro)
            isMinifyEnabled = true
            isShrinkResources = true
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
    // Push-уведомления RuStore (доставка через RuStore или приложения VK, без Google)
    implementation("ru.rustore.sdk:pushclient:7.5.0")
    // Версия из RuStore: обновление через RuStore и просьба оценить приложение
    implementation("ru.rustore.sdk:appupdate:10.5.1")
    implementation("ru.rustore.sdk:review:10.5.1")
    // AppMetrica: отчёты о вылетах и ошибках
    implementation("io.appmetrica.analytics:analytics:8.5.1")
    // Чат: скрипт до загрузки страницы (стили и данные клиента для виджета Битрикс24)
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
