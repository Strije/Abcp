package com.example.myapplication

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import retrofit2.Response
import java.io.IOException

suspend fun <T> performRequestWithRetry(
    attempts: Int = 3,
    initialDelayMs: Long = 700,
    request: suspend () -> Response<T>
): Response<T> {
    var lastError: Throwable? = null

    repeat(attempts) { index ->
        try {
            return request()
        } catch (e: CancellationException) {
            throw e
        } catch (e: java.io.InterruptedIOException) {
            // Таймаут: сервер запрос получил и думает (ABCP с онлайн-складами — до минуты). Повтор начнёт ожидание
            // заново и ответа не дождётся никогда — отдаём ошибку сразу (аудит 13.11: 3 × 30 с и «слишком долго»)
            throw e
        } catch (e: IOException) {
            lastError = e
            if (!currentCoroutineContext().isActive) throw e
            if (index == attempts - 1) throw e
            delay(initialDelayMs * (index + 1))
        }
    }

    throw lastError ?: IOException("Unknown network error")
}
