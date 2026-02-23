package com.example.myapplication.laximo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
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
            MaterialTheme { QuickGroupsScreen(ctx, repo) }
        }
    }
}

@Composable
private fun QuickGroupsScreen(ctx: LaximoVehicleContext, repo: LaximoRepository) {
    val scope = rememberCoroutineScope()
    val androidCtx = LocalContext.current

    var root by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var selectedGroup by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var categories by remember { mutableStateOf<List<LaximoPartsCategory>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }

    LaunchedEffect(ctx.catalog, ctx.ssd, ctx.vehicleId) {
        loading = true
        scope.launch {
            runCatching { repo.listQuickGroup(ctx) }
                .onSuccess { root = it }
                .onFailure { error = it.message }
            loading = false
        }
    }

    fun openUnit(unit: LaximoUnit) {
        val i = Intent(androidCtx, UnitDetailsActivity::class.java)
        i.putExtra("catalog", ctx.catalog)
        i.putExtra("vehicleId", ctx.vehicleId ?: "0")
        i.putExtra("unitSsd", unit.ssd)
        i.putExtra("unitId", unit.unitId)
        i.putExtra("unitName", unit.name)
        i.putExtra("imageUrl", resolveLaximoImageUrl(unit.largeImageUrl ?: unit.imageUrl, LaximoImageSize.P250))
        androidCtx.startActivity(i)
    }

    @OptIn(ExperimentalMaterial3Api::class)
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (selectedGroup == null) "Быстрый каталог" else (selectedGroup?.name ?: "Группа")) },
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

            if (loading) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }

            if (error != null) {
                Text(
                    "Ошибка: $error",
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(12.dp)
                )
            }

            if (selectedGroup == null) {
                val items = remember(root) { flattenQuick(root) }
                LazyColumn(Modifier.fillMaxSize()) {
                    items(items) { item ->
                        ListItem(
                            headlineContent = { Text(item.node.name ?: "Группа") },
                            supportingContent = {
                                item.node.synonyms?.let { Text(it, maxLines = 2) }
                            },
                            modifier = Modifier
                                .clickable(enabled = item.node.quickGroupId != null) {
                                    val gid = item.node.quickGroupId ?: return@clickable
                                    selectedGroup = item.node
                                    loading = true
                                    scope.launch {
                                        runCatching { repo.listQuickDetail(ctx, gid, all = true) }
                                            .onSuccess { categories = it }
                                            .onFailure { error = it.message }
                                        loading = false
                                    }
                                }
                                .padding(start = (item.level * 12).dp)
                        )
                        Divider()
                    }
                }
            } else {
                QuickDetailList(categories = categories, onOpenUnit = ::openUnit)
            }
        }
    }
}

@Composable
private fun QuickDetailList(
    categories: List<LaximoPartsCategory>,
    onOpenUnit: (LaximoUnit) -> Unit
) {
    LazyColumn(Modifier.fillMaxSize()) {
        items(categories) { cat ->
            Text(
                text = cat.name ?: "Категория",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)
            )

            val units = cat.units.orEmpty()
            units.forEach { u ->
                ListItem(
                    headlineContent = { Text(u.name) },
                    supportingContent = { u.code?.let { Text("Код: $it") } },
                    modifier = Modifier.clickable { onOpenUnit(u) }
                )
                Divider()
            }
        }
    }
}

private data class QuickFlat(val node: LaximoQuickGroupNode, val level: Int)

private fun flattenQuick(root: LaximoQuickGroupNode?): List<QuickFlat> {
    if (root == null) return emptyList()
    val out = mutableListOf<QuickFlat>()
    fun walk(n: LaximoQuickGroupNode, level: Int) {
        out += QuickFlat(n, level)
        n.children.forEach { walk(it, level + 1) }
    }
    walk(root, 0)
    return out
}
