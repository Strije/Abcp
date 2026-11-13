package com.example.myapplication.laximo

data class LaximoError(val code: String, val info: String? = null)

fun parseLaximoError(message: String): LaximoError {
    val idx = message.indexOf(':')
    return if (idx >= 0) {
        LaximoError(
            code = message.substring(0, idx).trim(),
            info = message.substring(idx + 1).trim().ifBlank { null }
        )
    } else {
        LaximoError(message.trim(), null)
    }
}

internal const val LAXIMO_TEXT_NOT_FOUND = "Не нашли такую машину. Проверьте номер — в VIN 17 знаков, без букв I, O и Q."
internal const val LAXIMO_TEXT_NO_CATALOG = "Каталог для этой машины сейчас недоступен. Напишите менеджеру — подберём вручную."
internal const val LAXIMO_TEXT_BUSY = "Слишком много запросов подряд. Подождите минуту и попробуйте ещё раз."
internal const val LAXIMO_TEXT_SLOW = "Каталог отвечает слишком долго. Попробуйте ещё раз."
internal const val LAXIMO_TEXT_NO_QUICK = "Быстрый подбор здесь недоступен. Откройте все узлы."
internal const val LAXIMO_TEXT_OFFLINE = "Нет связи с каталогом. Проверьте интернет и попробуйте ещё раз."
internal const val LAXIMO_TEXT_GENERAL = "Каталог временно недоступен. Попробуйте позже или напишите менеджеру в чате."

// В каких полях запроса приходит номер машины: ошибка в них = «машина не найдена»
private val vehicleFields = setOf("identstring", "vin", "frame", "frameno", "platenumber")

/** Код ошибки каталога (E_…) → фраза для покупателя. Неизвестный код — общая фраза, без самого кода. */
internal fun laximoCodeText(code: String?, info: String? = null): String = when (code?.trim()?.uppercase()) {
    "E_INVALIDPARAMETER" ->
        if (info != null && info.trim().lowercase() in vehicleFields) LAXIMO_TEXT_NOT_FOUND else LAXIMO_TEXT_GENERAL
    "E_CATALOGNOTEXISTS", "E_ACCESSDENIED", "E_NOTSUPPORTED" -> LAXIMO_TEXT_NO_CATALOG
    "E_GROUP_IS_NOT_SEARCHABLE" -> LAXIMO_TEXT_NO_QUICK
    "E_TOO_MANY_REQUESTS" -> LAXIMO_TEXT_BUSY
    "E_TIMEOUT" -> LAXIMO_TEXT_SLOW
    else -> LAXIMO_TEXT_GENERAL
}

/** Сырой текст ошибки каталога («E_…:поле») → фраза для покупателя. */
fun prettifyLaximoError(message: String): String {
    val e = parseLaximoError(message)
    return laximoCodeText(e.code, e.info)
}

/** Текст ошибки для покупателя: без «HTTP 500: {…}» и прочих технических подробностей. */
fun laximoUserMessage(e: Throwable?): String = run {
    if (e != null) com.example.myapplication.Analytics.error("Каталог Laximo", e)
    laximoErrorText(e)
}

/** То же без записи в аналитику — чистая функция (для тестов). */
internal fun laximoErrorText(e: Throwable?): String = when {
    e is LaximoApiException -> e.pretty
    // Нет связи с нашим сервером (code = 0) — у него свой понятный текст («Нет связи…», «Сервер не ответил…»)
    e is com.example.myapplication.server.ServerException && e.code == 0 ->
        e.message?.takeIf { it.isNotBlank() } ?: LAXIMO_TEXT_OFFLINE
    // Лимит запросов / подбор выключен на сервере — текст сервера понятен покупателю
    e is com.example.myapplication.server.ServerException && e.code in setOf(429, 503) ->
        e.message?.takeIf { it.isNotBlank() } ?: LAXIMO_TEXT_GENERAL
    e is java.net.UnknownHostException || e is java.net.ConnectException -> LAXIMO_TEXT_OFFLINE
    e is java.net.SocketTimeoutException || e is java.io.InterruptedIOException -> LAXIMO_TEXT_SLOW
    else -> LAXIMO_TEXT_GENERAL
}
