package com.example.myapplication.laximo

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Credentials
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

class LaximoClient(
    private val username: String,
    private val password: String,
    private val language: String = "ru_RU",
    private val baseUrl: String = "https://ws.laximo.ru"
) {
    private val http = OkHttpClient.Builder()
        .addInterceptor { chain ->
            val req = chain.request().newBuilder()
                .header("Authorization", Credentials.basic(username, password))
                .header("Accept-Language", language)
                .header("accept", "application/json")
                .build()
            chain.proceed(req)
        }
        .build()

    suspend fun post(operation: String, params: Map<String, String>): String = withContext(Dispatchers.IO) {
        val urlBuilder = (baseUrl.trimEnd('/') + "/restApi/v1/$operation")
            .toHttpUrl()
            .newBuilder()

        params.forEach { (k, v) -> urlBuilder.addQueryParameter(k, v) }
        val url = urlBuilder.build()

        Log.d("LAXIMO_HTTP", "--> POST $url")

        val request = Request.Builder()
            .url(url)
            // Laximo REST: POST, но параметры в query. Тело можно не отправлять.
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        http.newCall(request).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            Log.d("LAXIMO_HTTP", "<-- HTTP ${resp.code}")
            if (!resp.isSuccessful) throw RuntimeException("Laximo HTTP ${resp.code}: $body")
            body
        }
    }
}