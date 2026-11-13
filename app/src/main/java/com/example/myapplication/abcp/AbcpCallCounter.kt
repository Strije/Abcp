package com.example.myapplication.abcp

import android.content.Context
import com.example.myapplication.Analytics
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import okhttp3.Interceptor
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Имя метода ABCP по пути URL: первые два сегмента («/user/garage/add» → «user/garage», «/advices» → «advices»).
 * Только путь: параметры запроса (логин, пароль, номер) сюда не попадают.
 */
fun abcpMethodName(path: String): String {
    val segments = path.substringBefore('?').substringBefore('#').split('/')
        .filter { s -> s.isNotEmpty() && s.length <= 40 && s.all { it in 'a'..'z' || it in 'A'..'Z' || it in '0'..'9' || it == '_' } }
    return segments.take(2).joinToString("/").ifEmpty { "other" }
}

/**
 * Сколько запросов к ABCP делает это устройство, по методам. Лимит тарифа общий на весь магазин, и без счётчика
 * не видно, на что он уходит. Раз в сутки — одно событие «abcp_calls» {метод: число} за прошедший день.
 */
object AbcpCallCounter {
    private const val PREFS = "abcp_calls"
    private const val DAY = "day"
    private const val COUNTS = "counts"
    /** На диск — пачками, а не на каждый запрос */
    private const val FLUSH_EVERY = 20

    @Volatile private var app: Context? = null
    private val pending = HashMap<String, Int>()
    private var unsaved = 0

    /** Для всех клиентов OkHttp, которые ходят в ABCP */
    val interceptor = Interceptor { chain ->
        val request = chain.request()
        runCatching { count(request.url.encodedPath) }
        chain.proceed(request)
    }

    fun init(ctx: Context) {
        app = ctx.applicationContext
    }

    @Synchronized
    private fun count(path: String) {
        val name = abcpMethodName(path)
        pending[name] = (pending[name] ?: 0) + 1
        unsaved++
        if (unsaved >= FLUSH_EVERY) flush()
    }

    /**
     * Накопленное — на диск. Наступил новый день — сначала отправляем счётчик прошедшего дня и обнуляем.
     * Вызывается при запуске, при уходе экрана с глаз и после фоновой проверки заказов.
     */
    @Synchronized
    fun flush() {
        val ctx = app ?: return
        runCatching {
            val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val day = p.getString(DAY, null)
            if (day == today && pending.isEmpty()) return
            var saved = parse(p.getString(COUNTS, null))
            if (day != null && day != today) {
                if (saved.isNotEmpty()) Analytics.event("abcp_calls", saved)
                saved = emptyMap()
            }
            val merged = saved.toMutableMap()
            pending.forEach { (name, n) -> merged[name] = (merged[name] ?: 0) + n }
            pending.clear()
            unsaved = 0
            val json = JsonObject()
            merged.forEach { (name, n) -> json.addProperty(name, n) }
            p.edit().putString(DAY, today).putString(COUNTS, json.toString()).apply()
        }
    }

    private fun parse(json: String?): Map<String, Int> = runCatching {
        JsonParser.parseString(json ?: "{}").asJsonObject.entrySet().associate { it.key to it.value.asInt }
    }.getOrDefault(emptyMap())
}
