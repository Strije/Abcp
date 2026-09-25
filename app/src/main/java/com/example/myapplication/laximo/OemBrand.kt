package com.example.myapplication.laximo

/** Машина, для которой сейчас идёт подбор (ставит экран подбора) — чтобы «Цены» знали бренд оригинала. */
object CurrentCar {
    @Volatile var brand: String = ""
}

/**
 * Под каким брендом ABCP продаёт оригинал этой марки. Для остальных марок бренд совпадает с маркой
 * (или содержит её: Hyundai → «Hyundai-KIA»), это autoPickBrand находит сам.
 */
fun oemBrandFor(carBrand: String): String {
    val b = carBrand.uppercase().replace(Regex("[^A-Z]"), "")
    return when (b) {
        "VOLKSWAGEN", "VW", "AUDI", "SKODA", "SEAT", "CUPRA" -> "VAG"
        "LEXUS" -> "TOYOTA"
        "INFINITI" -> "NISSAN"
        "MINI" -> "BMW"
        "DACIA" -> "RENAULT"
        "CHEVROLET", "OPEL", "CADILLAC", "DAEWOO" -> "GENERAL MOTORS"
        "MERCEDESBENZ", "MERCEDES" -> "MERCEDES"
        "CITROEN", "PEUGEOT", "DS" -> b
        else -> carBrand.trim()
    }
}
