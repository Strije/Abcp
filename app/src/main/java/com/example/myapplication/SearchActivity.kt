package com.example.myapplication

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class SearchActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val brand = intent.getStringExtra(EXTRA_BRAND).orEmpty()
        val number = intent.getStringExtra(EXTRA_NUMBER).orEmpty()

        setContent {
            MaterialTheme {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(24.dp)
                ) {
                    Text("Экран поиска — следующий шаг")
                    Spacer(Modifier.height(16.dp))
                    Text("Бренд: $brand")
                    Text("Артикул: $number")
                }
            }
        }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
    }
}
