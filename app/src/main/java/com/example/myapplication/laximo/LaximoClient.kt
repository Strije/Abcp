package com.example.myapplication.laximo

import android.util.Log
import okhttp3.Credentials
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class LaximoClient(
    private val username: String,
    private val password: String,
    private val language: String = "ru_RU",
) {
    // ✅ Таймауты, чтобы запрос не "висел" бесконечно
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .build()

    private val baseUrl = "https://ws.laximo.ru/restApi/v1/"

    /**
     * Laximo REST API: только HTTPS + POST.
     * Параметры передаём в query string, body можно оставить пустым.
     */
    fun post(path: String, query: Map<String, String>): String {
        val urlBuilder = (baseUrl + path.trimStart('/')).toHttpUrl().newBuilder()

        query.forEach { (k, v) ->
            if (k.equals("ssd", ignoreCase = true) && looksEncoded(v)) {
                // SSD иногда уже приходит URL-encoded (%24...%3D%3D%24).
                // addEncodedQueryParameter не кодирует % повторно.
                urlBuilder.addEncodedQueryParameter(k, v)
            } else {
                urlBuilder.addQueryParameter(k, v)
            }
        }

        val url = urlBuilder.build()

        val req = Request.Builder()
            .url(url)
            .post("".toRequestBody("application/json".toMediaType()))
            .header("Authorization", Credentials.basic(username, password))
            .header("accept-language", language)
            .build()

        Log.d("LAXIMO_HTTP", "--> POST $url")

        http.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()

            // ✅ Теперь ты увидишь, что реально вернул сервер
            Log.d("LAXIMO_HTTP", "<-- HTTP ${resp.code}")
            Log.d("LAXIMO_HTTP", "BODY: $body")

            // Если HTTP не 2xx — это уже ошибка
            if (!resp.isSuccessful) {
                throw RuntimeException("Laximo HTTP ${resp.code}: $body")
            }

            // Иногда Laximo шлёт ошибку даже с 200 — ловим {"message":"E_...:..."}
            val parsed = LaximoErrorParser.tryParse(body)
            if (parsed != null) throw parsed

            return body
        }
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