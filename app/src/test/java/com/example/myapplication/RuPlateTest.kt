package com.example.myapplication

import com.example.myapplication.laximo.normalizeRuPlate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RuPlateTest {

    @Test
    fun recognizesPlates() {
        assertEquals("Н207ВН154", normalizeRuPlate("Н207ВН154"))
        assertEquals("А123ВС92", normalizeRuPlate("а 123 вс 92"))
        // латиница-двойник вместо кириллицы
        assertEquals("А123ВС777", normalizeRuPlate("A123BC777"))
    }

    @Test
    fun leavesVinAndFrameAlone() {
        assertNull(normalizeRuPlate("XTA210990Y2766389"))
        assertNull(normalizeRuPlate("SGL5-400683"))
        assertNull(normalizeRuPlate("Ж123ВС92")) // буквы Ж на номерах нет
    }
}
