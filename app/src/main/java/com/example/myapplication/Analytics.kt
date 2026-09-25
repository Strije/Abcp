package com.example.myapplication

import android.app.Application
import android.util.Log
import io.appmetrica.analytics.AppMetrica
import io.appmetrica.analytics.AppMetricaConfig

/**
 * AppMetrica (Яндекс): вылеты приложения и ошибки, которые видят клиенты, + несколько событий.
 * Только технические данные: экран, действие, текст ошибки, версия, модель телефона. Телефоны, имена,
 * логины и пароли сюда не отправляем. Без ключа (локальная отладка) ничего не шлётся.
 */
object Analytics {
    @Volatile private var active = false

    fun init(app: Application) {
        val key = BuildConfig.APPMETRICA_KEY
        if (key.isBlank() || BuildConfig.DEBUG) return
        runCatching {
            AppMetrica.activate(app, AppMetricaConfig.newConfigBuilder(key).withCrashReporting(true).build())
            active = true
        }.onFailure { Log.w("ANALYTICS", "AppMetrica не запущена", it) }
    }

    /** Ошибка, которую увидел клиент. where — «Экран → действие», по нему ошибки группируются в отчёте. */
    fun error(where: String, e: Throwable) {
        if (!active) return
        runCatching { AppMetrica.reportError(where, e.message ?: e.javaClass.simpleName, e) }
    }

    /** Событие без персональных данных: поиск, в корзину, заказ оформлен и т.п. */
    fun event(name: String, params: Map<String, Any?> = emptyMap()) {
        if (!active) return
        runCatching { AppMetrica.reportEvent(name, params.filterValues { it != null }) }
    }
}
