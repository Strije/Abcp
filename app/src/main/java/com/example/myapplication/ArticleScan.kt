package com.example.myapplication

import android.content.Context
import android.net.Uri
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * «Фото упаковки»: артикул с коробки — из штрихкодов и напечатанного текста (ML Kit, на телефоне).
 * Возвращаем кандидатов, человек выбирает нужный — на упаковке обычно несколько номеров.
 */
fun extractArticleCandidates(text: String, barcodes: List<String> = emptyList()): List<String> {
    val words = text.lines().flatMap { line ->
        // «W 712/75», «OC 90» печатают с пробелом — пробуем и всю строку, и слова по отдельности
        listOf(line.trim()) + line.split(Regex("[\\s,;:]+"))
    }
    val fromText = words.map { it.trim().trim('.', '-', '/', '(', ')') }
        .filter { w ->
            val compact = w.replace(" ", "")
            compact.length in 4..20 && compact.any { it.isDigit() } &&
                compact.all { it.isLetterOrDigit() || it in "-./" } &&
                !Regex("^\\d{1,2}[./]\\d{1,2}[./]\\d{2,4}$").matches(compact) && // даты
                w.count { it == ' ' } <= 1
        }
    // Буквы+цифры — вероятнее артикул, чем голое число (количество, вес, код партии)
    val ranked = fromText.sortedByDescending { w -> (if (w.any { it.isLetter() }) 2 else 0) + (if (' ' !in w) 1 else 0) }
    return (barcodes.filter { it.length in 4..24 } + ranked).distinctBy { it.uppercase().replace(" ", "") }.take(8)
}

suspend fun scanBarcodes(ctx: Context, uri: Uri): List<String> = suspendCancellableCoroutine { cont ->
    val scanner = BarcodeScanning.getClient()
    scanner.process(InputImage.fromFilePath(ctx, uri))
        .addOnSuccessListener { list -> cont.resumeWith(Result.success(list.mapNotNull { it.rawValue?.trim() })); scanner.close() }
        .addOnFailureListener { cont.resumeWith(Result.success(emptyList())); scanner.close() }
}
