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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.BuildConfig
import com.example.myapplication.UnitDetailsActivity
import com.example.myapplication.laximo.model.*
import kotlinx.coroutines.launch

/**
 * Экран "Быстрый каталог".
 * 1) listQuickGroup → корневое дерево быстрых групп
 * 2) listQuickDetail(quickGroupId) → категории/узлы/детали
 */
@OptIn(ExperimentalMaterial3Api::class)
class QuickGroupsActivity : ComponentActivity() {

    private val repo: LaximoRepository by lazy {
        LaximoRepository(
            LaximoClient(
                username = BuildConfig.LAXIMO_USER,
                password = BuildConfig.LAXIMO_PASS,
                language = "ru_RU"
            )
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val ssd = intent.getStringExtra("ssd").orEmpty()

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }

                var root by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
                var tabs by remember { mutableStateOf<List<LaximoQuickGroupNode>>(emptyList()) }
                var selectedTab by remember { mutableStateOf(0) }

                val scope = rememberCoroutineScope()

                // состояние: либо показываем группы, либо результат listQuickDetail
                var currentGroup by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
                var quickDetails by remember { mutableStateOf<List<LaximoPartsCategory>>(emptyList()) }
                var showingDetails by remember { mutableStateOf(false) }

                LaunchedEffect(catalog, vehicleId, ssd) {
                    loading = true
                    error = null
                    try {
                        val r = repo.listQuickGroup(ctx)
                        root = r
                        tabs = r.children
                        selectedTab = 0
                        currentGroup = tabs.firstOrNull()
                    } catch (e: Exception) {
                        error = e.message ?: e.javaClass.simpleName
                    } finally {
                        loading = false
                    }
                }

                fun openGroup(node: LaximoQuickGroupNode) {
                    currentGroup = node
                    val id = node.quickGroupId
                    if (id != null) {
                        showingDetails = true
                        loading = true
                        error = null
                        quickDetails = emptyList()
                        scope.launch {
                            try {
                                quickDetails = repo.listQuickDetail(ctx, id, all = false)
                            } catch (e: Exception) {
                                error = e.message ?: e.javaClass.simpleName
                            } finally {
                                loading = false
                            }
                        }
                    }
                }

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = { Text(if (showingDetails) "Быстрый каталог" else "Быстрые группы") },
                            navigationIcon = {
                                if (showingDetails) {
                                    IconButton(onClick = {
                                        showingDetails = false
                                        quickDetails = emptyList()
                                    }) {
                                        Text("←")
                                    }
                                }
                            }
                        )
                    }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(16.dp))
                            else -> {
                                if (!showingDetails) {
                                    Column(Modifier.fillMaxSize()) {
                                        if (tabs.isNotEmpty()) {
                                            ScrollableTabRow(selectedTabIndex = selectedTab) {
                                                tabs.forEachIndexed { idx, node ->
                                                    Tab(
                                                        selected = idx == selectedTab,
                                                        onClick = {
                                                            selectedTab = idx
                                                            currentGroup = tabs[idx]
                                                        },
                                                        text = { Text(node.name ?: "—") }
                                                    )
                                                }
                                            }
                                        }

                                        val group = currentGroup
                                        if (group == null) {
                                            Text("Нет быстрых групп", Modifier.padding(16.dp))
                                        } else {
                                            // показываем дочерние группы выбранной вкладки
                                            LazyColumn(
                                                modifier = Modifier.fillMaxSize(),
                                                contentPadding = PaddingValues(12.dp),
                                                verticalArrangement = Arrangement.spacedBy(10.dp)
                                            ) {
                                                items(group.children) { ch ->
                                                    ElevatedCard(
                                                        modifier = Modifier
                                                            .fillMaxWidth()
                                                            .clickable {
                                                                // если есть дети — просто "проваливаемся" в них
                                                                if (ch.children.isNotEmpty()) {
                                                                    currentGroup = ch
                                                                } else {
                                                                    openGroup(ch)
                                                                }
                                                            }
                                                    ) {
                                                        Column(Modifier.padding(12.dp)) {
                                                            Text(ch.name ?: "—", style = MaterialTheme.typography.titleMedium)
                                                            if (!ch.synonyms.isNullOrBlank()) {
                                                                Spacer(Modifier.height(4.dp))
                                                                Text(ch.synonyms!!, style = MaterialTheme.typography.bodySmall)
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                } else {
                                    // Показ результата listQuickDetail
                                    LazyColumn(
                                        modifier = Modifier.fillMaxSize(),
                                        contentPadding = PaddingValues(12.dp),
                                        verticalArrangement = Arrangement.spacedBy(10.dp)
                                    ) {
                                        items(quickDetails) { cat ->
                                            ElevatedCard(Modifier.fillMaxWidth()) {
                                                Column(Modifier.padding(12.dp)) {
                                                    Text(cat.name, style = MaterialTheme.typography.titleMedium)
                                                    Spacer(Modifier.height(6.dp))

                                                    cat.units.forEach { u ->
                                                        Text("• ${u.name}")
                                                        u.details.take(6).forEach { d ->
                                                            val label = buildString {
                                                                append("   - ")
                                                                append(d.name ?: "деталь")
                                                                if (!d.oem.isNullOrBlank()) append(" (${d.oem})")
                                                            }
                                                            Text(label, style = MaterialTheme.typography.bodySmall)
                                                        }

                                                        Spacer(Modifier.height(6.dp))
                                                        AssistChip(
                                                            onClick = {
                                                                // Открываем стандартный экран узла
                                                                val i = Intent(this@QuickGroupsActivity, UnitDetailsActivity::class.java)
                                                                i.putExtra("catalog", catalog)
                                                                i.putExtra("vehicleId", vehicleId)
                                                                i.putExtra("unitSsd", u.ssd)
                                                                i.putExtra("unitId", u.unitId)
                                                                i.putExtra("unitName", u.name)
                                                                i.putExtra("imageUrl", u.imageUrl.resolveLaximoImage(LaximoImageSize.SOURCE))
                                                                startActivity(i)
                                                            },
                                                            label = { Text("Открыть узел") }
                                                        )
                                                        Spacer(Modifier.height(10.dp))
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
            }
        }
    }
}
