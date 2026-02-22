package com.example.myapplication.laximo

import coil.request.ImageRequest
import okhttp3.Credentials

/**
 * Добавляет BasicAuth + язык к запросам изображений Laximo.
 * Это нужно, потому что картинки часто защищены теми же учётными данными, что и REST API.
 */
fun ImageRequest.Builder.laximoAuth(
    username: String,
    password: String,
    language: String = "ru_RU",
): ImageRequest.Builder =
    this
        .addHeader("Authorization", Credentials.basic(username, password))
        .addHeader("accept-language", language)
