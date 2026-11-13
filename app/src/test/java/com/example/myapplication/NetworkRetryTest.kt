package com.example.myapplication

import com.example.myapplication.ui.waitingText
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response
import java.io.IOException
import java.net.SocketTimeoutException

class NetworkRetryTest {
    @Test
    fun timeoutIsNotRetried() {
        // ABCP думает до минуты: повтор после таймаута начинал ожидание заново и не дожидался ответа никогда
        var calls = 0
        val failed = runCatching {
            runBlocking {
                performRequestWithRetry<String>(initialDelayMs = 1) { calls++; throw SocketTimeoutException("timeout") }
            }
        }
        assertTrue(failed.exceptionOrNull() is SocketTimeoutException)
        assertEquals(1, calls)
    }

    @Test
    fun brokenConnectionIsRetried() {
        var calls = 0
        val r = runBlocking {
            performRequestWithRetry(initialDelayMs = 1) {
                if (++calls < 3) throw IOException("reset") else Response.success("ok")
            }
        }
        assertEquals("ok", r.body())
        assertEquals(3, calls)
    }

    @Test
    fun waitingTextChangesWithTime() {
        assertEquals("Ищем номер…", waitingText(0, "Ищем номер…"))
        assertNotEquals(waitingText(5), waitingText(20))
        assertNotEquals(waitingText(20), waitingText(50))
    }
}
