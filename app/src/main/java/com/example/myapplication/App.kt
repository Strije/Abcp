package com.example.myapplication

import android.app.Application
import android.util.Log
import ru.rustore.sdk.pushclient.RuStorePushClient

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Analytics.init(this) // вылеты и ошибки — в AppMetrica
        Vpn.init(this)
        com.example.myapplication.push.Fcm.init(this) // второй канал push — Google (до регистрации токенов)
        // Каждый открытый экран — событие screen (карта путей в AppMetrica); вкладки главного экрана шлёт он сам
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityResumed(a: android.app.Activity) {
                if (a !is MainShellActivity) Analytics.screen(a.javaClass.simpleName.removeSuffix("Activity"))
            }
            override fun onActivityCreated(a: android.app.Activity, b: android.os.Bundle?) {}
            override fun onActivityStarted(a: android.app.Activity) {}
            override fun onActivityPaused(a: android.app.Activity) {}
            override fun onActivityStopped(a: android.app.Activity) {}
            override fun onActivitySaveInstanceState(a: android.app.Activity, b: android.os.Bundle) {}
            override fun onActivityDestroyed(a: android.app.Activity) {}
        })
        com.example.myapplication.server.ServerAddress.init(this) // адрес сервера можно сменить страницей avtodrug92.ru/api
        runCatching { com.example.myapplication.abcp.ensureNotificationChannels(this) }
        // RuStore Push SDK требует инициализации именно здесь. Нет ID проекта — работаем без push.
        if (BuildConfig.RUSTORE_PROJECT_ID.isNotBlank()) {
            runCatching { RuStorePushClient.init(application = this, projectId = BuildConfig.RUSTORE_PROJECT_ID) }
                .onFailure { Log.w("PUSH", "RuStore push не инициализирован", it) }
        }
    }
}
