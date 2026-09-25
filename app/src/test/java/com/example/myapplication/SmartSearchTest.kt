package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SmartSearchTest {

    @Test
    fun vin() {
        assertTrue(looksLikeVin("XTA210990Y2766389"))
        assertTrue(looksLikeVin("wauzzz8k9ba123456"))
        assertFalse(looksLikeVin("04E115561H"))          // артикул
        assertFalse(looksLikeVin("XTA21099OY2766389"))   // буква O в VIN недопустима
    }

    @Test
    fun frame() {
        assertTrue(looksLikeFrame("SGL5-400683"))
        assertTrue(looksLikeFrame("gx100-6012345"))
        assertFalse(looksLikeFrame("04E115561H"))
        assertFalse(looksLikeFrame("OC90"))
    }

    @Test
    fun phoneMask() {
        assertEquals("9781234567", com.example.myapplication.ui.phoneDigits("+7 (978) 123-45-67"))
        assertEquals("9781234567", com.example.myapplication.ui.phoneDigits("8 978 123 45 67"))
        assertEquals("978", com.example.myapplication.ui.phoneDigits("978"))
        assertEquals("(978) 123-45-67", com.example.myapplication.ui.formatPhoneDigits("9781234567"))
        assertEquals("(978) 12", com.example.myapplication.ui.formatPhoneDigits("97812"))
        assertEquals("79781234567", normalizeMobile("9781234567"))
    }

    @Test
    fun passwordRules() {
        assertEquals(null, passwordProblem("Avtodrug2026"))
        assertTrue(passwordProblem("test123456")!!.contains("заглавная"))
        assertTrue(passwordProblem("Ab1")!!.contains("8 символов"))
    }
}
