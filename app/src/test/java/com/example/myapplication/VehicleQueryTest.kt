package com.example.myapplication

import com.example.myapplication.laximo.normalizeVehicleQuery
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VehicleQueryTest {

    @Test
    fun cleansVin() {
        // пробелы, дефисы, строчные буквы
        assertEquals("XTA210990Y2766389", normalizeVehicleQuery(" xta 21099-0y27 66389 ").value)
        assertNull(normalizeVehicleQuery("XTA210990Y2766389").hint)
        // русские буквы-двойники вместо латинских (ХТА → XTA)
        assertEquals("XTA210990Y2766389", normalizeVehicleQuery("ХТА210990У2766389").value)
    }

    @Test
    fun hintsWhenVinLengthIsWrong() {
        val short = normalizeVehicleQuery("XTA210990Y276638")
        assertEquals("XTA210990Y276638", short.value)
        assertEquals("В VIN 17 знаков, у вас 16. Проверьте номер.", short.hint)
        assertEquals("В VIN 17 знаков, у вас 18. Проверьте номер.", normalizeVehicleQuery("XTA210990Y27663890").hint)
        assertEquals("В VIN 17 знаков, у вас 15. Проверьте номер.", normalizeVehicleQuery("xta 210990 y27663").hint)
        // короче 15 или длиннее 19 — на VIN не похоже, ищем как есть
        assertNull(normalizeVehicleQuery("XTA210990Y2766").hint)
        assertNull(normalizeVehicleQuery("XTA210990Y2766389000").hint)
    }

    @Test
    fun keepsFrameNumbers() {
        // дефис в номере кузова нужен
        assertEquals("GX100-1234567", normalizeVehicleQuery("gx100-1234567").value)
        assertNull(normalizeVehicleQuery("GX100-1234567").hint)
        assertEquals("SGL5-400683", normalizeVehicleQuery("sgl5 - 400683").value)
        // кузов, набранный русскими буквами
        assertEquals("CE110-1234567", normalizeVehicleQuery("СЕ110-1234567").value)
        // длинный номер с дефисом — не VIN, подсказку не показываем
        assertNull(normalizeVehicleQuery("JZX100-012345678").hint)
    }

    @Test
    fun keepsPlates() {
        assertEquals("А123ВС92", normalizeVehicleQuery("a 123 bc 92").value)
        assertEquals("Н207ВН154", normalizeVehicleQuery("Н207ВН154").value)
        assertNull(normalizeVehicleQuery("Н207ВН154").hint)
    }
}
