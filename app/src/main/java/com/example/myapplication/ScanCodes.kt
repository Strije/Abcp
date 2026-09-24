package com.example.myapplication

import com.example.myapplication.laximo.normalizeRuPlate

/** Что нашлось на фото: VIN, госномер или номер кузова. */
data class VehicleCode(val value: String, val kind: String) {
    val title: String get() = when (kind) {
        "vin" -> "VIN"
        "plate" -> "Госномер"
        else -> "Номер кузова"
    }
}

/**
 * Разбор текста, распознанного с фото (табличка VIN, СТС, номерной знак).
 * Распознаватель латинский: русские буквы номера он читает как латинские двойники —
 * normalizeRuPlate переводит их обратно. В VIN не бывает I, O, Q — их чиним на 1, 0, 0.
 */
fun extractVehicleCodes(text: String): List<VehicleCode> {
    val out = LinkedHashMap<String, VehicleCode>()
    val lines = text.uppercase().lines().map { it.trim() }.filter { it.isNotEmpty() }

    for (line in lines) {
        val tokens = line.split(Regex("[^A-ZА-Я0-9]+")).filter { it.isNotEmpty() }

        // VIN: слово из 17 знаков; распознаватель иногда рвёт его пробелом — склеиваем соседние куски,
        // только если вместе ровно 17 (иначе к VIN прилипла бы подпись «VIN» со СТС)
        for (i in tokens.indices) {
            for (n in 1..3) {
                if (i + n > tokens.size) break
                val raw = tokens.subList(i, i + n).joinToString("")
                if (raw.length != 17 || raw.any { it !in 'A'..'Z' && it !in '0'..'9' }) continue
                val vin = raw.replace('I', '1').replace('O', '0').replace('Q', '0')
                if (looksLikeVin(vin)) out.putIfAbsent(vin, VehicleCode(vin, "vin"))
            }
        }

        // Госномер: «А 123 ВС 92», «A123BC 92», «А123ВС|92»
        for (i in tokens.indices) {
            for (n in 1..4) {
                if (i + n > tokens.size) break
                val plate = normalizeRuPlate(tokens.subList(i, i + n).joinToString(""))
                if (plate != null) out.putIfAbsent(plate, VehicleCode(plate, "plate"))
            }
        }

        // Номер кузова японских авто: «GX100-6012345»
        Regex("[A-Z]{2,4}\\d{0,3}[A-Z]?\\s?-\\s?\\d{4,7}").findAll(line).forEach { m ->
            val frame = m.value.replace(" ", "")
            if (looksLikeFrame(frame)) out.putIfAbsent(frame, VehicleCode(frame, "frame"))
        }
    }
    // Сначала VIN (точнее всего подбирает), потом номер, потом кузов
    return out.values.sortedBy { listOf("vin", "plate", "frame").indexOf(it.kind) }
}
