package com.example.myapplication.abcp

import com.example.myapplication.AbcpErrorDto
import com.google.gson.Gson

private fun parseAbcpError(rawBody: String?): AbcpErrorDto? {
    if (rawBody.isNullOrBlank()) return null
    return try {
        Gson().fromJson(rawBody, AbcpErrorDto::class.java)
    } catch (_: Exception) {
        null
    }
}

/** Код ошибки ABCP (102 — логин/пароль, 103 — нет прав на операцию) или null. */
fun abcpErrorCode(rawBody: String?): Int? = parseAbcpError(rawBody)?.errorCode

fun prettifyAbcpError(rawBody: String?): String {
    val err = parseAbcpError(rawBody)

    val message = cleanAbcpMessage(err?.errorMessage)

    return when (err?.errorCode) {
        // IP-фильтр у магазина выключен, так что 103 — это не включённые клиенту права на API
        103 -> "Для вашего аккаунта ещё не включён доступ из приложения. Отправьте заявку на главном экране — менеджер включит."
        102 -> message ?: "Неправильный логин или пароль!"
        else -> message ?: (rawBody ?: "Ошибка")
    }
}