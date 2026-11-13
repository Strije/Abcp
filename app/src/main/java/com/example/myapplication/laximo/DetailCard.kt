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

// Названия для атрибутов, у которых каталог не прислал своего
private val attributeTitles = mapOf("note" to "Примечание", "amount" to "Количество")

/**
 * Атрибуты детали строками «Название: значение» (примечание, применимость, лев/прав, количество, годы).
 * Пустые и то, что карточка уже показывает (название, номер, номер на схеме), пропускаем.
 */
internal fun detailAttributeLines(d: LaximoDetail): List<String> {
    val shown = listOfNotNull(d.name, d.oem, d.codeOnImage).map { it.trim().lowercase() }.toSet()
    return d.attributes.mapNotNull { a ->
        val value = a.value?.trim().orEmpty()
        if (value.isEmpty() || value.lowercase() in shown) return@mapNotNull null
        val title = a.name?.trim()?.takeIf { it.isNotEmpty() }
            ?: attributeTitles[a.key.trim().lowercase()]
            ?: a.key.trim()
        if (title.isEmpty()) value else "$title: $value"
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
