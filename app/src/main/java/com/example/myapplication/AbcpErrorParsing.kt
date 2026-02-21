package com.example.myapplication

import com.google.gson.Gson

private fun parseAbcpError(rawBody: String?): AbcpErrorDto? {
    if (rawBody.isNullOrBlank()) return null
    return try {
        Gson().fromJson(rawBody, AbcpErrorDto::class.java)
    } catch (_: Exception) {
        null
    }
}

fun prettifyAbcpError(rawBody: String?): String {
    val err = parseAbcpError(rawBody)

    val message = err?.errorMessage?.takeIf { it.isNotBlank() }

    return when (err?.errorCode) {
        103 -> "Доступ запрещён для этого IP.\nОтключите VPN или попросите менеджера добавить IP."
        102 -> message ?: "Неправильный логин или пароль!"
        else -> message ?: (rawBody ?: "Ошибка")
    }
}