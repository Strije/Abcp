package com.example.myapplication.laximo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.model.LaximoPartsCategory
import com.example.myapplication.laximo.model.LaximoQuickGroupNode
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class QuickGroupsActivity : ComponentActivity() {

    private val repo = LaximoRepository()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog") ?: ""
        val vehicleId = intent.getStringExtra("vehicleId") ?: "0"
        val ssd = intent.getStringExtra("ssd") ?: ""

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)

        setContent {
            com.example.myapplication.ui.theme.AvtodrugTheme { QuickGroupsScreen(ctx, repo) }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun QuickGroupsScreen(ctx: LaximoVehicleContext, repo: LaximoRepository) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var root by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var selectedGroup by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var categories by remember { mutableStateOf<List<LaximoPartsCategory>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }

    var searchQuery by remember { mutableStateOf("") }
    var expandedIds by remember { mutableStateOf(emptySet<Long>()) }

    LaunchedEffect(Unit) {
        loading = true
        runCatching { repo.listQuickGroup(ctx) }
            .onSuccess { root = it }
            .onFailure { error = laximoUserMessage(it) }
        loading = false
    }

    fun openUnit(unit: LaximoUnit) {
        val i = Intent(context, UnitDetailsActivity::class.java).apply {
            putExtra("catalog", ctx.catalog)
            putExtra("vehicleId", ctx.vehicleId)
            putExtra("unitSsd", unit.ssd)
            putExtra("unitId", unit.unitId)
            putExtra("unitName", unit.name)
            putExtra("imageUrl", resolveLaximoImageUrl(unit.largeImageUrl ?: unit.imageUrl, LaximoImageSize.SOURCE))
        }
        context.startActivity(i)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(selectedGroup?.name ?: "Быстрый подбор") },
                actions = {
                    if (selectedGroup == null) {
                        IconButton(onClick = {
                            val i = Intent(context, CatalogCategoriesActivity::class.java).apply {
                                putExtra("catalog", ctx.catalog)
                                putExtra("vehicleId", ctx.vehicleId)
                                putExtra("ssd", ctx.ssd)
                            }
                            context.startActivity(i)
                        }) {
                            Icon(Icons.AutoMirrored.Filled.List, contentDescription = "Каталог")
                        }
                    }
                },
                navigationIcon = {
                    if (selectedGroup != null) {
                        TextButton(onClick = {
                            selectedGroup = null
                            categories = emptyList()
                            error = null
                        }) { Text("Назад") }
                    }
                }
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())

            if (selectedGroup == null) {
                OutlinedTextField(
                    value = searchQuery,
                    onValueChange = { searchQuery = it },
                    label = { Text("Поиск запчасти...") },
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    singleLine = true
                )

                if (error != null) {
                    Text(text = error!!, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(12.dp))
                }

                data class SearchHit(
                    val node: LaximoQuickGroupNode,
                    val level: Int,
                    // Путь по родительским узлам — без него в плоском списке результатов
                    // поиска теряется контекст вроде "Передние тормоза" / "Задние тормоза".
                    val ancestorPath: String
                )

                val displayItems = remember(root, searchQuery, expandedIds) {
                    val result = mutableListOf<SearchHit>()
                    val q = searchQuery.trim().lowercase()

                    fun walk(node: LaximoQuickGroupNode, level: Int, ancestorPath: String) {
                        val nameMatch = node.name.lowercase().contains(q)
                        val synMatch = node.synonyms?.lowercase()?.contains(q) == true
                        val matches = q.isEmpty() || nameMatch || synMatch
                        val childPath = if (ancestorPath.isEmpty()) node.name else "$ancestorPath › ${node.name}"

                        if (q.isNotEmpty()) {
                            if (matches) {
                                result.add(SearchHit(node, 0, ancestorPath))
                            }
                            node.children.forEach { walk(it, level + 1, childPath) }
                        } else {
                            result.add(SearchHit(node, level, ancestorPath))
                            val id = node.quickGroupId
                            if (id != null && expandedIds.contains(id)) {
                                node.children.forEach { walk(it, level + 1, childPath) }
                            }
                        }
                    }
                    root?.children?.forEach { walk(it, 0, "") }
                    result
                }

                LazyColumn(Modifier.fillMaxSize()) {
                    itemsIndexed(displayItems, key = { index, item ->
                        // Индекс в списке гарантирует уникальность, даже если один и тот же
                        // узел встречается в дереве под разными родителями (частый случай
                        // при поиске: "Фильтры"/"Колодки" есть в нескольких категориях сразу).
                        "${index}_${item.node.quickGroupId}_${item.node.name.hashCode()}"
                    }) { _, hit ->
                        val node = hit.node
                        val level = hit.level
                        ListItem(
                            headlineContent = { Text(node.name) },
                            supportingContent = {
                                val subtitle = listOfNotNull(
                                    hit.ancestorPath.takeIf { it.isNotEmpty() },
                                    node.synonyms?.takeIf { it.isNotBlank() }
                                ).joinToString("  •  ")
                                if (subtitle.isNotEmpty()) Text(subtitle, maxLines = 1)
                            },
                            modifier = Modifier
                                .clickable {
                                    val gid = node.quickGroupId ?: return@clickable
                                    if (node.children.isNotEmpty() && searchQuery.isEmpty()) {
                                        expandedIds = if (expandedIds.contains(gid)) {
                                            expandedIds - gid
                                        } else {
                                            expandedIds + gid
                                        }
                                    } else {
                                        loading = true
                                        error = null
                                        scope.launch {
                                            runCatching { repo.listQuickDetail(ctx, gid, all = true) }
                                                .onSuccess {
                                                    categories = it
                                                    selectedGroup = node
                                                }
                                                .onFailure { error = laximoUserMessage(it) }
                                            loading = false
                                        }
                                    }
                                }
                                .padding(start = (level * 16).dp)
                        )
                        HorizontalDivider()
                    }
                }
            } else {
                if (error != null) {
                    Text(text = error!!, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                }
                
                LazyColumn(Modifier.fillMaxSize()) {
                    categories.forEach { cat ->
                        item(key = "header_${cat.name}") {
                            Surface(
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text(
                                    text = cat.name,
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                                    style = MaterialTheme.typography.titleSmall,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                        }
                        items(cat.units, key = { "unit_${it.unitId}" }) { unit ->
                            ListItem(
                                headlineContent = { Text(unit.name) },
                                supportingContent = { unit.code?.let { Text("Код: $it") } },
                                modifier = Modifier.clickable { openUnit(unit) }
                            )
                            HorizontalDivider()
                        }
                    }
                }
            }
        }
    }
}
