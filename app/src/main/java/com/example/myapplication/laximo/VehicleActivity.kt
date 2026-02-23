package com.example.myapplication.laximo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class VehicleActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog") ?: ""
        val vehicleId = intent.getStringExtra("vehicleId") ?: "0"
        val ssd = intent.getStringExtra("ssd") ?: ""
        val brand = intent.getStringExtra("brand") ?: ""
        val name = intent.getStringExtra("name") ?: ""
        val title = listOf(brand, name).filter { it.isNotBlank() }.joinToString(" ")

        setContent {
            MaterialTheme {
                VehicleScreen(
                    title = title,
                    onGroupsClick = {
                        val i = Intent(this, CatalogCategoriesActivity::class.java)
                        i.putExtra("catalog", catalog)
                        i.putExtra("vehicleId", vehicleId)
                        i.putExtra("ssd", ssd)
                        startActivity(i)
                    },
                    onQuickGroupsClick = {
                        val i = Intent(this, QuickGroupsActivity::class.java)
                        i.putExtra("catalog", catalog)
                        i.putExtra("vehicleId", vehicleId)
                        i.putExtra("ssd", ssd)
                        startActivity(i)
                    }
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun VehicleScreen(
    title: String,
    onGroupsClick: () -> Unit,
    onQuickGroupsClick: () -> Unit,
) {
    Scaffold(
        topBar = { TopAppBar(title = { Text(title) }) }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Button(
                onClick = onGroupsClick,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Каталог по группам запчастей")
            }
            Button(
                onClick = onQuickGroupsClick,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Быстрый подбор запчастей")
            }
        }
    }
}
