package com.example.myapplication

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.LaximoApiException
import com.example.myapplication.laximo.LaximoClient
import com.example.myapplication.laximo.LaximoRepository
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class LaximoUnitUi(val id: String, val name: String)

@OptIn(ExperimentalMaterial3Api::class)
class CatalogUnitsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val ssd = intent.getStringExtra("ssd").orEmpty()
        val categoryId = intent.getStringExtra("categoryId").orEmpty()
        val categoryName = intent.getStringExtra("categoryName").orEmpty()

        val repo = LaximoRepository(
            LaximoClient(
                username = BuildConfig.LAXIMO_USER,
                password = BuildConfig.LAXIMO_PASS,
                language = "ru_RU"
            )
        )

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var units by remember { mutableStateOf<List<LaximoUnitUi>>(emptyList()) }

                LaunchedEffect(Unit) {
                    try {
                        val raw = withContext(Dispatchers.IO) {
                            repo.listUnits(catalog, vehicleId, ssd, categoryId)
                        }
                        Log.d("LAXIMO_CAT", "units raw=$raw")
                        units = parseUnits(raw)
                    } catch (e: LaximoApiException) {
                        error = e.pretty
                    } catch (e: Exception) {
                        Log.e("LAXIMO_CAT", "EX=${e.message}", e)
                        error = "Ошибка загрузки узлов."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text(categoryName.ifBlank { "Узлы" }) }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.padding(24.dp))
                            error != null -> Text(error!!, Modifier.padding(24.dp))
                            else -> LazyColumn(
                                contentPadding = PaddingValues(16.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                items(units) { u ->
                                    ElevatedCard(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clickable {
                                                // Следующий шаг: UnitDetailsActivity (схема + OEM)
                                                // Пока просто лог:
                                                Log.d("LAXIMO_CAT", "open unit id=${u.id}")
                                            }
                                    ) {
                                        Text(u.name, Modifier.padding(16.dp))
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

fun parseUnits(raw: String): List<LaximoUnitUi> {
    val root = JsonParser.parseString(raw)
    val list = mutableListOf<LaximoUnitUi>()

    fun addFromArray(arrName: String): Boolean {
        val obj = root.asJsonObject
        if (!obj.has(arrName)) return false
        val arr = obj.getAsJsonArray(arrName)
        arr.forEach { el ->
            val o = el.asJsonObject
            val id = (o["unitId"] ?: o["id"])?.asString ?: return@forEach
            val name = (o["name"] ?: o["title"])?.asString ?: id
            list += LaximoUnitUi(id, name)
        }
        return list.isNotEmpty()
    }

    if (root.isJsonObject) {
        if (addFromArray("units")) return list
        if (addFromArray("unit")) return list
        if (addFromArray("data")) return list
    }

    if (root.isJsonArray) {
        root.asJsonArray.forEach { el ->
            val o = el.asJsonObject
            val id = (o["unitId"] ?: o["id"])?.asString ?: return@forEach
            val name = (o["name"] ?: o["title"])?.asString ?: id
            list += LaximoUnitUi(id, name)
        }
    }

    return list
}