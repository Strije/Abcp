package com.example.myapplication

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import retrofit2.Response
import java.io.IOException

suspend fun <T> performRequestWithRetry(
    attempts: Int = 4,
    initialDelayMs: Long = 500,
    request: suspend () -> Response<T>
): Response<T> {
    var lastError: Throwable? = null

    repeat(attempts) { index ->
        try {
            return withContext(Dispatchers.IO) { request() }
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            lastError = e
            if (!currentCoroutineContext().isActive) throw e
            if (index == attempts - 1) throw e
            delay(initialDelayMs * (index + 1) * (index + 1))
        }
    }

    throw lastError ?: IOException("Unknown network error")
}
