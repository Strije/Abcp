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

fun prettifyLaximoError(message: String): String {
    val e = parseLaximoError(message)
    fun withInfo(base: String) = if (e.info != null) "$base (${e.info})" else base

    return when (e.code) {
        "E_INVALIDREQUEST" -> "Неверно сформирован запрос к Laximo."
        "E_INVALIDPARAMETER" -> withInfo("Неверное значение параметра")
        "E_CATALOGNOTEXISTS" -> withInfo("Каталог не зарегистрирован")
        "E_UNKNOWNCOMMAND" -> withInfo("Неизвестная операция")
        "E_ACCESSDENIED" -> withInfo("Доступ запрещён")
        "E_NOTSUPPORTED" -> withInfo("Операция не поддерживается каталогом")
        "E_TOO_MANY_REQUESTS" -> withInfo("Превышен лимит запросов. Попробуйте позже")
        "E_TIMEOUT" -> withInfo("Превышено время ожидания")
        "E_UNEXPECTED_PROBLEM" -> "Сбой сервиса Laximo."
        else -> message
    }
}

/** Текст ошибки для покупателя: без «HTTP 500: {…}» и прочих технических подробностей. */
fun laximoUserMessage(e: Throwable?): String = when {
    e is LaximoApiException -> e.pretty
    e is java.net.UnknownHostException || e is java.net.ConnectException ->
        "Нет связи с каталогом. Проверьте интернет и попробуйте ещё раз."
    e is java.net.SocketTimeoutException || e is java.io.InterruptedIOException ->
        "Каталог отвечает слишком долго. Попробуйте ещё раз."
    else -> "Каталог временно недоступен. Попробуйте позже или спросите менеджера в чате."
}
