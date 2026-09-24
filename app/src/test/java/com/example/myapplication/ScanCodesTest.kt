package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanCodesTest {

    @Test
    fun vinFromPlateWithSpacesAndOcrMistakes() {
        // распознаватель вставил пробел и прочитал 0 как O
        val codes = extractVehicleCodes("VIN\nXTA21O99 0Y2766389")
        assertEquals("XTA210990Y2766389", codes.first { it.kind == "vin" }.value)
    }

    @Test
    fun plateFromLatinLookalikes() {
        val codes = extractVehicleCodes("A 123 BC 92 RUS")
        assertEquals("А123ВС92", codes.single { it.kind == "plate" }.value)
    }

    @Test
    fun stsGivesVinAndPlate() {
        val sts = """
            СВИДЕТЕЛЬСТВО О РЕГИСТРАЦИИ ТС
            Регистрационный знак H207BH154
            Идентификационный номер (VIN) KNAPH81BDM5123456
            Марка, модель KIA SPORTAGE
        """.trimIndent()
        val codes = extractVehicleCodes(sts)
        assertEquals(listOf("vin", "plate"), codes.map { it.kind })
        assertEquals("KNAPH81BDM5123456", codes[0].value)
        assertEquals("Н207ВН154", codes[1].value)
    }

    @Test
    fun frameNumber() {
        assertTrue(extractVehicleCodes("Frame No. GX100 - 6012345").any { it.value == "GX100-6012345" && it.kind == "frame" })
    }

    @Test
    fun noiseGivesNothing() {
        assertEquals(0, extractVehicleCodes("МАСЛО 5W-40 4Л\nЦЕНА 3745").size)
    }
}
