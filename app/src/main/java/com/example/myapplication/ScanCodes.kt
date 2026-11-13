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

// ---------------- Фото для распознавания ----------------

/** Длинная сторона фото для распознавания, px: тексту и штрихкоду хватает, а в память помещается любой снимок. */
internal const val SCAN_LONG_SIDE = 2000

/**
 * Во сколько раз уменьшить снимок при декодировании (степень двойки, как требует BitmapFactory):
 * длинная сторона остаётся не меньше [target] и меньше 2×[target].
 */
internal fun scanSampleSize(width: Int, height: Int, target: Int = SCAN_LONG_SIDE): Int {
    val longSide = maxOf(width, height)
    var sample = 1
    while (longSide / (sample * 2) >= target) sample *= 2
    return sample
}

/** Уменьшенный снимок и угол (0/90/180/270), на который его нужно повернуть по EXIF. */
class ScanPhoto(val bitmap: android.graphics.Bitmap, val rotation: Int)

/**
 * Фото для распознавания: декодируем сразу уменьшенным и не на главном потоке. Снимок 50–108 Мп
 * в полном размере не помещается в память, а OutOfMemoryError обычный catch (e: Exception) не ловит —
 * поэтому нехватку памяти и нечитаемый файл отдаём как IOException.
 */
suspend fun loadScanPhoto(ctx: android.content.Context, uri: android.net.Uri): ScanPhoto =
    kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
        try {
            val resolver = ctx.contentResolver
            val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
            resolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it, null, bounds) }
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw java.io.IOException("Фото не читается")
            val opts = android.graphics.BitmapFactory.Options().apply {
                inSampleSize = scanSampleSize(bounds.outWidth, bounds.outHeight)
            }
            val bitmap = resolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it, null, opts) }
                ?: throw java.io.IOException("Фото не читается")
            val orientation = runCatching {
                resolver.openInputStream(uri)?.use {
                    android.media.ExifInterface(it).getAttributeInt(
                        android.media.ExifInterface.TAG_ORIENTATION, android.media.ExifInterface.ORIENTATION_NORMAL
                    )
                }
            }.getOrNull()
            val rotation = when (orientation) {
                android.media.ExifInterface.ORIENTATION_ROTATE_90 -> 90
                android.media.ExifInterface.ORIENTATION_ROTATE_180 -> 180
                android.media.ExifInterface.ORIENTATION_ROTATE_270 -> 270
                else -> 0
            }
            ScanPhoto(bitmap, rotation)
        } catch (e: OutOfMemoryError) {
            throw java.io.IOException("Фото слишком большое")
        } catch (e: SecurityException) {
            throw java.io.IOException("Нет доступа к фото")
        }
    }
