package com.example.myapplication.laximo

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.model.LaximoDetail

// Названия атрибутов по-русски: каталог часть из них присылает английскими ключами («side», «usage», «Range»)
private val attributeTitles = mapOf(
    "side" to "Сторона",
    "usage" to "Применимость",
    "range" to "Годы выпуска",
    "note" to "Примечание", "notes" to "Примечание",
    "amount" to "Количество", "quantity" to "Количество", "qty" to "Количество",
    "model" to "Модель",
    "engine" to "Двигатель",
    "transmission" to "Коробка передач",
    "body" to "Кузов",
    "color" to "Цвет",
    "size" to "Размер",
    "position" to "Расположение",
    "date" to "Дата",
    "options" to "Комплектация", "option" to "Комплектация",
    "description" to "Описание"
)

private val sideValues = mapOf("l" to "левая", "lh" to "левая", "r" to "правая", "rh" to "правая")

private fun capitalized(s: String): String = s.replaceFirstChar { it.uppercaseChar() }

/**
 * Название атрибута для человека: известное английское — по словарю (без учёта регистра),
 * русское от каталога — как есть, неизвестное — как пришло, но с заглавной буквы.
 */
internal fun attributeTitle(name: String?, key: String): String {
    val n = name?.trim().orEmpty()
    val k = key.trim()
    attributeTitles[n.lowercase()]?.let { return it }
    // Каталог прислал своё название по-русски — оно точнее словаря
    if (n.any { it in 'а'..'я' || it in 'А'..'Я' || it == 'ё' || it == 'Ё' }) return capitalized(n)
    attributeTitles[k.lowercase()]?.let { return it }
    return capitalized(n.ifEmpty { k })
}

/** Значение атрибута: у «Стороны» L/LH и R/RH — словами. */
internal fun attributeValue(title: String, value: String): String =
    if (title == "Сторона") sideValues[value.trim().lowercase()] ?: value else value

/**
 * Атрибуты детали строками «Название: значение» (примечание, применимость, лев/прав, количество, годы).
 * Пустые и то, что карточка уже показывает (название, номер, номер на схеме), пропускаем.
 */
internal fun detailAttributeLines(d: LaximoDetail): List<String> {
    val shown = listOfNotNull(d.name, d.oem, d.codeOnImage).map { it.trim().lowercase() }.toSet()
    return d.attributes.mapNotNull { a ->
        val value = a.value?.trim().orEmpty()
        if (value.isEmpty() || value.lowercase() in shown) return@mapNotNull null
        val title = attributeTitle(a.name, a.key)
        if (title.isEmpty()) value else "$title: ${attributeValue(title, value)}"
    }.distinct()
}

@Composable
fun DetailCard(
    d: LaximoDetail,
    selected: Boolean = false,
    onClick: () -> Unit = {},
    onCartClick: (String) -> Unit = {}
) {
    val oem = d.oem?.trim().orEmpty()
    val isOemEmpty = oem.isBlank()

    Card(
        modifier = Modifier
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = if (selected) {
            CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
        } else {
            CardDefaults.cardColors()
        }
    ) {
        Row(
            modifier = Modifier
                .padding(12.dp)
                .fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(d.name ?: "Деталь", style = MaterialTheme.typography.titleSmall)
                if (!isOemEmpty) {
                    Spacer(Modifier.height(4.dp))
                    Text("Оригинальный номер: $oem", style = MaterialTheme.typography.bodySmall)
                }
                d.codeOnImage?.let { code ->
                    if (code.isNotBlank()) {
                        Spacer(Modifier.height(2.dp))
                        Text("Номер на схеме: $code", style = MaterialTheme.typography.bodySmall)
                    }
                }
                // Примечание, применимость, сторона, количество — без них легко заказать не ту деталь
                detailAttributeLines(d).forEach { line ->
                    Spacer(Modifier.height(2.dp))
                    Text(
                        line,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            // Оригинальный номер → поиск в магазине: цены, наличие, аналоги
            if (!isOemEmpty) {
                Spacer(Modifier.width(8.dp))
                FilledTonalButton(onClick = { onCartClick(oem) }, contentPadding = PaddingValues(horizontal = 12.dp)) {
                    Text("Цены")
                }
            }
        }
    }
}
