package com.example.myapplication.server

import android.content.Context
import com.example.myapplication.BuildConfig
import com.google.gson.JsonParser
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.security.KeyFactory
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Адрес нашего сервера, который можно сменить без выпуска версии (переезд на другой хостинг/IP).
 *
 * Где лежит: страница сайта avtodrug92.ru/api в ABCP в режиме «без шаблона» (как новости news1…4) со строкой
 * `server=https://новый-адрес`. Приложение работает с последним рабочим адресом (впервые — зашитым в сборку),
 * а в фоне — раз в 6 часов и при каждом сбое связи с сервером — читает страницу.
 *
 * Новый адрес принимаем, только если по нему отвечает наш /health (опечатка на странице приложение не сломает)
 * И сервер доказал, что он наш: подписал случайную строку закрытым ключом, парным к зашитому BuildConfig.SERVER_KEY.
 * Иначе тот, кто получит доступ к правке страницы сайта, увёл бы логины всех клиентов на свой сервер.
 * Если сохранённый адрес умер, а нового подтверждённого нет — возвращаемся на зашитый в сборку.
 */
object ServerAddress {
    private const val PREFS = "server_address" // не abcp_session: выход из аккаунта адрес не сбрасывает
    private const val KEY = "url"
    private const val CHECKED = "checked"
    private const val EVERY = 6 * 3_600_000L
    const val CONFIG_URL = "https://avtodrug92.ru/api"
    private const val NONCE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    private var app: Context? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()

    fun init(ctx: Context) {
        app = ctx.applicationContext
        refreshInBackground(force = false)
    }

    /** Зашитый в сборку адрес — ему верим без подписи. */
    private val builtIn: String get() = BuildConfig.SERVER_URL.trimEnd('/')

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
                val now = current
                val page = fetch(CONFIG_URL)
                if (page != null) prefs.edit().putLong(CHECKED, System.currentTimeMillis()).apply()
                val next = page?.let { parse(it) }
                var moved = false
                if (next != null && next != now && isAlive(next)) {
                    when {
                        // Страница вернула нас на зашитый адрес — сохранённый больше не нужен
                        next == builtIn -> {
                            prefs.edit().remove(KEY).apply()
                            moved = true
                        }
                        confirmed(next) -> {
                            prefs.edit().putString(KEY, next).apply()
                            moved = true
                        }
                        else -> com.example.myapplication.Analytics.event("server_move_rejected", mapOf("to" to next))
                    }
                    if (moved) com.example.myapplication.Analytics.event("server_moved", mapOf("to" to next))
                }
                // Откат: сохранённый адрес не отвечает, нового подтверждённого нет, а зашитый жив
                if (!moved && now != builtIn && builtIn.isNotBlank() && !isAlive(now) && isAlive(builtIn)) {
                    prefs.edit().remove(KEY).apply()
                    com.example.myapplication.Analytics.event("server_fallback", mapOf("from" to now))
                }
            } catch (_: Exception) {
                // фоновая проверка не должна ронять приложение; адрес остаётся прежним
            } finally {
                running.set(false)
            }
        }
    }

    /** Сервер по этому адресу подписал нашу случайную строку ключом, парным к зашитому открытому. */
    private fun confirmed(base: String): Boolean {
        val key = BuildConfig.SERVER_KEY
        if (key.isBlank()) return false
        val random = SecureRandom()
        val nonce = buildString { repeat(32) { append(NONCE_CHARS[random.nextInt(NONCE_CHARS.length)]) } }
        val body = fetch("$base/v1/identity?nonce=$nonce") ?: return false
        val sig = runCatching { JsonParser.parseString(body).asJsonObject["sig"].asString }.getOrNull() ?: return false
        return verifyServerSignature(key, nonce, sig)
    }

    private fun fetch(url: String): String? = runCatching {
        http.newCall(Request.Builder().url(url).build()).execute().use { r -> if (r.isSuccessful) r.body?.string() else null }
    }.getOrNull()

    private fun isAlive(base: String) = runCatching {
        http.newCall(Request.Builder().url("$base/health").build()).execute().use { it.isSuccessful }
    }.getOrDefault(false)

    /**
     * `server=https://…` в тексте страницы (HTML-теги вокруг не мешают). Только https.
     * На странице могут быть и другие строки «ключ=значение» — их пока не читаем (и `old_server=` за адрес не примем).
     */
    fun parse(page: String): String? =
        Regex("(?<![A-Za-z0-9_])server\\s*=\\s*(https://[A-Za-z0-9.\\-:]+)").find(page)?.groupValues?.get(1)?.trimEnd('/')
}

/**
 * Подпись сервера: ECDSA P-256 / SHA-256 (DER) над строкой «avtodrug92-server:» + nonce.
 * publicKeyB64 — открытый ключ X.509 в Base64. Любая ошибка (мусор, не тот ключ) — false.
 * (Ed25519 на Android до 13 нет, поэтому ECDSA.)
 */
internal fun verifyServerSignature(publicKeyB64: String, nonce: String, sigB64: String): Boolean = try {
    val key = decodeBase64(publicKeyB64)
    val sig = decodeBase64(sigB64)
    if (key == null || sig == null || nonce.isEmpty()) {
        false
    } else {
        val pub = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(key))
        val check = Signature.getInstance("SHA256withECDSA")
        check.initVerify(pub)
        check.update("avtodrug92-server:$nonce".toByteArray(Charsets.UTF_8))
        check.verify(sig)
    }
} catch (_: Exception) {
    false
}

/**
 * Свой разбор Base64: java.util.Base64 появился только в Android 8 (у нас от 7), а android.util.Base64
 * не работает в юнит-тестах. null — в строке есть посторонние знаки.
 */
internal fun decodeBase64(text: String): ByteArray? {
    val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    val clean = text.filterNot { it.isWhitespace() }.trimEnd('=')
    if (clean.isEmpty()) return null
    val out = java.io.ByteArrayOutputStream()
    var buffer = 0
    var bits = 0
    for (c in clean) {
        val v = alphabet.indexOf(c)
        if (v < 0) return null
        buffer = ((buffer shl 6) or v) and 0xFFFF // старше 16 бит уже записано
        bits += 6
        if (bits >= 8) {
            bits -= 8
            out.write((buffer shr bits) and 0xFF)
        }
    }
    return out.toByteArray()
}
