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
                Spacer(Modifier.height(4.dp))
                Text("OEM: ${if (isOemEmpty) "-" else oem}", style = MaterialTheme.typography.bodySmall)
                d.codeOnImage?.let { code ->
                    if (code.isNotBlank()) {
                        Spacer(Modifier.height(2.dp))
                        Text("Номер на схеме: $code", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            IconButton(
                onClick = { if (!isOemEmpty) onCartClick(oem) },
                enabled = !isOemEmpty
            ) {
                Icon(
                    imageVector = Icons.Default.ShoppingCart,
                    contentDescription = "Поиск",
                    tint = if (isOemEmpty) {
                        MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                    } else {
                        MaterialTheme.colorScheme.primary
                    }
                )
            }
        }
    }
}
