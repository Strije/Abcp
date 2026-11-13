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
    /** Текст для покупателя: без кодов и технических подробностей. */
    val pretty: String
        get() = if (code != null) laximoCodeText(code, extra) else prettifyLaximoError(rawMessage.orEmpty())
}
