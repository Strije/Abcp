package com.example.myapplication.laximo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoVehicleContext
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.ErrorState
import com.example.myapplication.ui.theme.AvtodrugTheme

/** «Все узлы»: разделы каталога автомобиля (двигатель, подвеска, …) → узлы раздела. */
@OptIn(ExperimentalMaterial3Api::class)
class CatalogCategoriesActivity : ComponentActivity() {

    private val repo by lazy { LaximoRepository(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val catalog = intent.getStringExtra("catalog")
        val vehicleId = intent.getStringExtra("vehicleId")
        val ssd = intent.getStringExtra("ssd")
        if (catalog == null || vehicleId == null || ssd == null) { finish(); return }
        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)

        setContent {
            AvtodrugTheme {
                val context = LocalContext.current
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var categories by remember { mutableStateOf<List<LaximoCategory>>(emptyList()) }
                var reload by remember { mutableIntStateOf(0) }

                LaunchedEffect(reload) {
                    loading = true
                    error = null
                    try {
                        categories = repo.listCategories(ctx)
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Каталог → категории", e)
                        error = laximoUserMessage(e)
                    } finally {
                        loading = false
                    }
                }

                Scaffold(topBar = { TopAppBar(title = { Text("Все узлы") }) }) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> ErrorState(error!!, Modifier.align(Alignment.Center), onRetry = { reload++ })
                            categories.isEmpty() -> EmptyState("Каталог для этой машины пуст — спросите менеджера в чате, подберём вручную.")
                            else -> LazyColumn(Modifier.fillMaxSize()) {
                                itemsIndexed(categories, key = { i, c -> "${i}_${c.categoryId}" }) { _, cat ->
                                    ListItem(
                                        headlineContent = { Text(cat.name) },
                                        trailingContent = { Text("›", style = MaterialTheme.typography.titleMedium) },
                                        modifier = Modifier.clickable {
                                            // На экран узлов — SSD категории, а не исходный
                                            context.startActivity(
                                                Intent(context, CatalogUnitsActivity::class.java)
                                                    .putExtra("catalog", catalog)
                                                    .putExtra("vehicleId", vehicleId)
                                                    .putExtra("ssd", cat.ssd)
                                                    .putExtra("categoryId", cat.categoryId)
                                                    .putExtra("categoryName", cat.name)
                                            )
                                        }
                                    )
                                    HorizontalDivider()
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
