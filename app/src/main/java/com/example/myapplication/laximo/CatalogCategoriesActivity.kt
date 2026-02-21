package com.example.myapplication

import android.content.Intent
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

data class LaximoCategoryUi(
    val id: String,
    val name: String
)

@OptIn(ExperimentalMaterial3Api::class)
class CatalogCategoriesActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val ssd = intent.getStringExtra("ssd").orEmpty()
        val brand = intent.getStringExtra("brand").orEmpty()
        val name = intent.getStringExtra("name").orEmpty()

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
                var categories by remember { mutableStateOf<List<LaximoCategoryUi>>(emptyList()) }

                LaunchedEffect(Unit) {
                    try {
                        val raw = withContext(Dispatchers.IO) {
                            repo.listCategories(catalog, vehicleId, ssd)
                        }
                        Log.d("LAXIMO_CAT", "categories raw=$raw")
                        categories = parseCategories(raw)
                    } catch (e: LaximoApiException) {
                        error = e.pretty
                    } catch (e: Exception) {
                        Log.e("LAXIMO_CAT", "EX=${e.message}", e)
                        error = "Ошибка загрузки категорий."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text("$brand $name") }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.padding(24.dp))
                            error != null -> Text(error!!, Modifier.padding(24.dp))
                            else -> LazyColumn(
                                contentPadding = PaddingValues(16.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                items(categories) { c ->
                                    ElevatedCard(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clickable {
                                                startActivity(
                                                    Intent(this@CatalogCategoriesActivity, CatalogUnitsActivity::class.java).apply {
                                                        putExtra("catalog", catalog)
                                                        putExtra("vehicleId", vehicleId)
                                                        putExtra("ssd", ssd)
                                                        putExtra("brand", brand)
                                                        putExtra("car_name", name)
                                                        putExtra("categoryId", c.id)
                                                        putExtra("categoryName", c.name)
                                                    }
                                                )
                                            }
                                    ) {
                                        Text(c.name, Modifier.padding(16.dp))
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

fun parseCategories(raw: String): List<LaximoCategoryUi> {
    // Формат ответа зависит от сервиса, поэтому делаем максимально терпимый парсер:
    // ищем массив "categories" или "category"
    val root = JsonParser.parseString(raw)
    val list = mutableListOf<LaximoCategoryUi>()

    fun tryArray(path: String): Boolean {
        val obj = root.asJsonObject
        if (!obj.has(path)) return false
        val arr = obj.getAsJsonArray(path)
        arr.forEach { el ->
            val o = el.asJsonObject
            val id = (o["categoryId"] ?: o["id"])?.asString ?: return@forEach
            val name = (o["name"] ?: o["title"])?.asString ?: id
            list += LaximoCategoryUi(id, name)
        }
        return list.isNotEmpty()
    }

    // Частые варианты ключей
    if (root.isJsonObject) {
        if (tryArray("categories")) return list
        if (tryArray("category")) return list
        if (tryArray("data")) return list
    }

    // Если вдруг пришёл массив
    if (root.isJsonArray) {
        root.asJsonArray.forEach { el ->
            val o = el.asJsonObject
            val id = (o["categoryId"] ?: o["id"])?.asString ?: return@forEach
            val name = (o["name"] ?: o["title"])?.asString ?: id
            list += LaximoCategoryUi(id, name)
        }
    }

    return list
}