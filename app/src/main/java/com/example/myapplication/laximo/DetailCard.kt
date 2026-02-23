package com.example.myapplication.laximo

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.model.LaximoDetail

@Composable
fun DetailCard(d: LaximoDetail, selected: Boolean = false) {
    Card(
        modifier = Modifier
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .fillMaxWidth(),
        colors = if (selected) {
            CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
        } else {
            CardDefaults.cardColors()
        }
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(d.name ?: "Деталь", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(4.dp))
            Text("OEM: ${d.oem ?: "-"}", style = MaterialTheme.typography.bodySmall)
            d.codeOnImage?.let { code ->
                Spacer(Modifier.height(2.dp))
                Text("Номер на схеме: $code", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
