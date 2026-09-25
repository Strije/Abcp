package com.example.myapplication

import android.app.Application
import android.util.Log
import ru.rustore.sdk.pushclient.RuStorePushClient

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Analytics.init(this) // вылеты и ошибки — в AppMetrica
        runCatching { com.example.myapplication.abcp.ensureNotificationChannels(this) }
        // RuStore Push SDK требует инициализации именно здесь. Нет ID проекта — работаем без push.
        if (BuildConfig.RUSTORE_PROJECT_ID.isNotBlank()) {
            runCatching { RuStorePushClient.init(application = this, projectId = BuildConfig.RUSTORE_PROJECT_ID) }
                .onFailure { Log.w("PUSH", "RuStore push не инициализирован", it) }
        }
    }
}
