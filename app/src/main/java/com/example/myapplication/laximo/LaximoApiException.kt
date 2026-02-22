package com.example.myapplication.laximo

class LaximoApiException(
    val code: String? = null,
    val extra: String? = null,
    val rawMessage: String? = null
) : Exception(
    rawMessage ?: buildString {
        append(code ?: "Laximo error")
        if (!extra.isNullOrBlank()) append(":").append(extra)
    }
) {
    val pretty: String
        get() = rawMessage?.let { prettifyLaximoError(it) }
            ?: message.orEmpty()
}