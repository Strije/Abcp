package com.example.myapplication

import com.example.myapplication.abcp.Probe
import com.example.myapplication.abcp.accessConfirmed
import com.example.myapplication.abcp.deniedKeys
import com.example.myapplication.abcp.probeDue
import com.example.myapplication.abcp.probeOutcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiAccessTest {

    @Test
    fun flagOnlyWhenEveryProbePassed() {
        assertTrue(accessConfirmed(listOf(Probe.Ok, Probe.Ok)))
        // Сбой сервера — «не знаем»: флаг не пишем
        assertFalse(accessConfirmed(listOf(Probe.Ok, Probe.Unknown)))
        assertFalse(accessConfirmed(listOf(Probe.Ok, Probe.Denied)))
        assertFalse(accessConfirmed(emptyList()))
    }

    @Test
    fun probeOutcomeByAnswer() {
        assertEquals(Probe.Denied, probeOutcome(400, """{"errorCode":103,"errorMessage":"Access denied"}"""))
        assertEquals(Probe.Denied, probeOutcome(403, null))
        // «Ничего не нашлось» — запрос принят, право есть
        assertEquals(Probe.Ok, probeOutcome(404, """{"errorCode":301,"errorMessage":"No results"}"""))
        assertEquals(Probe.Unknown, probeOutcome(502, "<html>Bad Gateway</html>"))
        assertEquals(Probe.Unknown, probeOutcome(500, null))
    }

    @Test
    fun probesNotOftenThanSixHours() {
        val hour = 3_600_000L
        val now = 1_000 * hour
        assertTrue(probeDue(0, now))                   // ещё не проверяли
        assertFalse(probeDue(now - 1, now))
        assertFalse(probeDue(now - 6 * hour + 1, now))
        assertTrue(probeDue(now - 6 * hour, now))
        assertTrue(probeDue(now + hour, now))          // часы перевели назад — проверяем
    }

    @Test
    fun deniedKeysOnlyDenied() {
        assertEquals(listOf("basket"), deniedKeys(linkedMapOf("brands" to Probe.Ok, "basket" to Probe.Denied, "orders" to Probe.Unknown)))
    }

    @Test
    fun emailCheck() {
        assertNull(emailProblem(""))
        assertNull(emailProblem("ivan@mail.ru"))
        assertTrue(emailProblem("ivan") != null)
        assertTrue(emailProblem("ivan@mail") != null)
        assertTrue(emailProblem("@mail.ru") != null)
        assertTrue(emailProblem("ivan@mail.") != null)
        assertTrue(emailProblem("ivan @mail.ru") != null)
    }
}
