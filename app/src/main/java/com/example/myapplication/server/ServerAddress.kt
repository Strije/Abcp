package com.example.myapplication.server

import android.content.Context
import com.example.myapplication.BuildConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Адрес нашего сервера, который можно сменить без выпуска версии (переезд на другой хостинг/IP).
 *
 * Где лежит: страница сайта avtodrug92.ru/api в ABCP в режиме «без шаблона» (как новости news1…4) с одной строкой
 * `server=https://новый-адрес`. Приложение работает с последним рабочим адресом (впервые — зашитым в сборку),
 * а в фоне — раз в 6 часов и при каждом сбое связи с сервером — читает страницу. Новый адрес принимаем, только
 * если по нему отвечает наш /health: опечатка на странице приложение не сломает.
 */
object ServerAddress {
    private const val PREFS = "server_address" // не abcp_session: выход из аккаунта адрес не сбрасывает
    private const val KEY = "url"
    private const val CHECKED = "checked"
    private const val EVERY = 6 * 3_600_000L
    const val CONFIG_URL = "https://avtodrug92.ru/api"

    private var app: Context? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()

    fun init(ctx: Context) {
        app = ctx.applicationContext
        refreshInBackground(force = false)
    }

    /** Текущий адрес без «/» на конце. */
    val current: String
        get() = (app?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)?.getString(KEY, null) ?: BuildConfig.SERVER_URL)
            .trimEnd('/')

    /** Сервер не ответил — возможно, переехал: перечитать страницу (не чаще одной проверки за раз). */
    fun onNetworkFailure() = refreshInBackground(force = true)

    private fun refreshInBackground(force: Boolean) {
        val ctx = app ?: return
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!force && System.currentTimeMillis() - prefs.getLong(CHECKED, 0) < EVERY) return
        if (!running.compareAndSet(false, true)) return
        scope.launch {
            try {
                val page = fetch(CONFIG_URL) ?: return@launch
                prefs.edit().putLong(CHECKED, System.currentTimeMillis()).apply()
                val next = parse(page) ?: return@launch
                if (next != current && isAlive(next)) {
                    prefs.edit().putString(KEY, next).apply()
                    com.example.myapplication.Analytics.event("server_moved", mapOf("to" to next))
                }
            } finally {
                running.set(false)
            }
        }
    }

    private fun fetch(url: String): String? = runCatching {
        http.newCall(Request.Builder().url(url).build()).execute().use { r -> if (r.isSuccessful) r.body?.string() else null }
    }.getOrNull()

    private fun isAlive(base: String) = runCatching {
        http.newCall(Request.Builder().url("$base/health").build()).execute().use { it.isSuccessful }
    }.getOrDefault(false)

    /** `server=https://…` в тексте страницы (HTML-теги вокруг не мешают). Только https. */
    fun parse(page: String): String? =
        Regex("server\\s*=\\s*(https://[A-Za-z0-9.\\-:]+)").find(page)?.groupValues?.get(1)?.trimEnd('/')
}
