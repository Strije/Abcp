package com.example.myapplication.laximo

import android.content.Context
import android.net.Uri
import android.util.Log
import com.example.myapplication.BuildConfig
import com.example.myapplication.server.AppServer
import com.example.myapplication.server.ServerException

/**
 * Laximo REST API через наш сервер (/v1/laximo/{метод}): логин и пароль Laximo живут только на сервере,
 * в APK их нет. Ответы и ошибки (E_…) приходят как от самого Laximo.
 */
class LaximoClient(ctx: Context) {

    private val server = AppServer(ctx.applicationContext)

    fun post(path: String, query: Map<String, String>): String {
        // SSD иногда уже приходит URL-encoded (%24...%3D%3D%24) — сервер закодирует сам, отдаём исходный
        val params = query.mapValues { (k, v) -> if (k.equals("ssd", ignoreCase = true) && looksEncoded(v)) Uri.decode(v) else v }
        val method = path.trimStart('/')
        Log.d("LAXIMO_HTTP", "--> $method")
        val (code, body) = server.laximo(method, params)
        Log.d("LAXIMO_HTTP", "<-- HTTP $code")
        if (BuildConfig.DEBUG) Log.d("LAXIMO_HTTP", "BODY: $body")

        // Laximo шлёт ошибку и с 200 — ловим {"message":"E_...:..."}
        LaximoErrorParser.tryParse(body)?.let { throw it }
        if (code !in 200..299) {
            val detail = runCatching {
                com.google.gson.JsonParser.parseString(body).asJsonObject["detail"].asString
            }.getOrNull()
            throw ServerException(detail ?: "Каталог временно недоступен ($code)", code)
        }
        return body
    }
}

private fun looksEncoded(value: String): Boolean {
    return Regex("%[0-9A-Fa-f]{2}").containsMatchIn(value)
}

private object LaximoErrorParser {
    fun tryParse(body: String): LaximoApiException? {
        // минимальный парсер: ловим {"message":"E_...:..."}
        val m = Regex("\"message\"\\s*:\\s*\"([^\"]+)\"")
            .find(body)
            ?.groupValues
            ?.getOrNull(1)
            ?: return null

        if (!m.startsWith("E_")) return null

        val parts = m.split(":", limit = 2)
        val code = parts[0]
        val extra = parts.getOrNull(1)

        return LaximoApiException(code = code, extra = extra)
    }
}
