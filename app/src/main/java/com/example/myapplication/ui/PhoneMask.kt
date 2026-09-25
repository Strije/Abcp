package com.example.myapplication.ui

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation

/**
 * Российский мобильный: в поле храним только 10 цифр после +7, показываем «(978) 123-45-67».
 * «+7 » — префикс поля. Вставка «+7 978…» или «8 978…» тоже понимается.
 */
fun phoneDigits(input: String): String {
    var d = input.filter { it.isDigit() }
    if (d.length >= 11 && (d[0] == '7' || d[0] == '8')) d = d.drop(1)
    return d.take(10)
}

/** «9781234567» → «(978) 123-45-67» (частично введённый номер — частично оформлен). */
fun formatPhoneDigits(d: String): String = buildString {
    d.forEachIndexed { i, c ->
        when (i) {
            0 -> append('(')
            3 -> append(") ")
            6, 8 -> append('-')
        }
        append(c)
    }
}

object RuPhoneMask : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val d = text.text
        val out = formatPhoneDigits(d)
        // Позиция каждой цифры в оформленной строке — чтобы курсор стоял там, где ожидает человек
        val pos = IntArray(d.length + 1)
        var j = 0
        for (i in d.indices) {
            while (j < out.length && out[j] != d[i]) j++
            pos[i] = j
            j++
        }
        pos[d.length] = out.length
        return TransformedText(AnnotatedString(out), object : OffsetMapping {
            override fun originalToTransformed(offset: Int) = pos[offset.coerceIn(0, d.length)]
            override fun transformedToOriginal(offset: Int): Int {
                var r = 0
                for (i in 0..d.length) if (pos[i] <= offset) r = i
                return r
            }
        })
    }
}
