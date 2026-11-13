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
import androidx.compose.ui.text.style.TextOverflow
import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoVehicleContext
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.ErrorState
import com.example.myapplication.ui.theme.AvtodrugTheme

/**
 * «Все узлы»: разделы каталога автомобиля (двигатель, подвеска, …) → узлы раздела.
 * Раздел с подразделами открывает этот же экран уровнем ниже (parentId) — «назад» возвращает на уровень выше.
 */
@OptIn(ExperimentalMaterial3Api::class)
class CatalogCategoriesActivity : ComponentActivity() {

    private val repo by lazy { LaximoRepository(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val catalog = intent.getStringExtra("catalog")
        val vehicleId = intent.getStringExtra("vehicleId")
        val ssd = intent.getStringExtra("ssd")
        if (catalog == null || vehicleId == null || ssd == null) { finish(); return }
        // Подразделы раздела: его номер и название; ssd в этом случае — SSD раздела
        val parentId = intent.getStringExtra("parentId")?.takeIf { it.isNotBlank() }
        val parentName = intent.getStringExtra("parentName").orEmpty()
        // Машина — подзаголовком и дальше по цепочке экранов (бренд нужен кнопке «Цены»)
        val brand = intent.getStringExtra("brand").orEmpty()
        val carName = intent.getStringExtra("name").orEmpty()
        val car = "$brand $carName".trim()
        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)

        // На экран узлов — SSD категории, а не исходный
        fun unitsIntent(categoryId: String, categoryName: String, categorySsd: String) =
            Intent(this, CatalogUnitsActivity::class.java)
                .putExtra("catalog", catalog)
                .putExtra("vehicleId", vehicleId)
                .putExtra("ssd", categorySsd)
                .putExtra("categoryId", categoryId)
                .putExtra("categoryName", categoryName)
                .putExtra("brand", brand)
                .putExtra("name", carName)

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
                        val list = repo.listCategories(ctx, parentId)
                        if (parentId != null && list.isEmpty()) {
                            // Подразделов каталог не дал — показываем узлы самого раздела
                            startActivity(unitsIntent(parentId, parentName, ssd))
                            finish()
                            return@LaunchedEffect
                        }
                        categories = list
                    } catch (e: kotlinx.coroutines.CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Каталог → категории", e)
                        error = laximoUserMessage(e)
                    } finally {
                        loading = false
                    }
                }

                Scaffold(topBar = {
                    TopAppBar(
                        navigationIcon = { com.example.myapplication.ui.BackButton() },
                        title = {
                            Column {
                                Text(parentName.ifBlank { "Все узлы" }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                if (car.isNotBlank()) Text(
                                    car, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis
                                )
                            }
                        }
                    )
                }) { padding ->
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
                                            if (cat.childrens) {
                                                // Раздел с подразделами — тот же экран уровнем ниже
                                                context.startActivity(
                                                    Intent(context, CatalogCategoriesActivity::class.java)
                                                        .putExtra("catalog", catalog)
                                                        .putExtra("vehicleId", vehicleId)
                                                        .putExtra("ssd", cat.ssd)
                                                        .putExtra("parentId", cat.categoryId)
                                                        .putExtra("parentName", cat.name)
                                                        .putExtra("brand", brand)
                                                        .putExtra("name", carName)
                                                )
                                            } else {
                                                context.startActivity(unitsIntent(cat.categoryId, cat.name, cat.ssd))
                                            }
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
