package com.example.myapplication

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
}
