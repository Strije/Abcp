// ApiClient.kt
package com.example.myapplication.abcp

import com.example.myapplication.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {

    // Адрес API магазина в ABCP (должен заканчиваться на /)
    private const val BASE_URL = "https://id25202.public.api.abcp.ru/"

    private val loggingInterceptor by lazy {
        HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BASIC
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }
    }

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            // Поиск с онлайн-складами поставщиков ABCP отдаёт за 7–64 с (замер 10.11) — 30 с обрывали его всегда
            .callTimeout(75, TimeUnit.SECONDS)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(75, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            // OkHttp сам повторяет запрос при обрыве соединения — для basket/add это двойное количество.
            // Чтение повторяет performRequestWithRetry
            .retryOnConnectionFailure(false)
            // Учёт запросов по методам — видно, на что уходит суточный лимит ABCP
            .addInterceptor(AbcpCallCounter.interceptor)
            .addInterceptor(loggingInterceptor)
            .build()
    }

    private val retrofit: Retrofit by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(httpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    private val api: AbcpApi by lazy {
        retrofit.create(AbcpApi::class.java)
    }

    fun create(): AbcpApi = api
}
