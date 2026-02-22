// ApiClient.kt
package com.example.myapplication

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {

    // Поставь СВОЙ baseUrl (важно: должен заканчиваться на /)
    // Например: https://id25202.public.api.abcp.ru/
    private const val BASE_URL = "https://id25202.public.api.abcp.ru/"

    fun create(): AbcpApi {
        val logging = HttpLoggingInterceptor().apply {
            // Для диагностики лучше BODY, потом можно BASIC или NONE
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .writeTimeout(25, TimeUnit.SECONDS)
            .callTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

        return retrofit.create(AbcpApi::class.java)
    }
}