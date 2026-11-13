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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material3.*
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class QuickGroupsActivity : ComponentActivity() {

    private val repo by lazy { LaximoRepository(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog") ?: ""
        val vehicleId = intent.getStringExtra("vehicleId") ?: "0"
        val ssd = intent.getStringExtra("ssd") ?: ""

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)
        // Машина — в заголовке, чтобы было видно, для чего подбираем
        val brand = intent.getStringExtra("brand").orEmpty()
        val carName = intent.getStringExtra("name").orEmpty()
        CurrentCar.brand = brand

        setContent {
            com.example.myapplication.ui.theme.AvtodrugTheme {
                QuickGroupsScreen(ctx, repo, brand, carName, intent.getStringExtra("query").orEmpty())
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun QuickGroupsScreen(
    ctx: LaximoVehicleContext, repo: LaximoRepository, brand: String, carName: String, initialQuery: String = ""
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val car = "$brand $carName".trim()

    var root by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var selectedGroup by remember { mutableStateOf<LaximoQuickGroupNode?>(null) }
    var categories by remember { mutableStateOf<List<LaximoPartsCategory>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    // Дерево групп: запрос завершился (пусть и пустым ответом) / каталог ответил, что быстрого подбора нет
    var treeLoaded by remember { mutableStateOf(false) }
    var treeUnavailable by remember { mutableStateOf(false) }
    // Загрузка выбранной группы: пока идёт, повторные нажатия не запускают её ещё раз
    var groupJob by remember { mutableStateOf<Job?>(null) }

    var searchQuery by remember { mutableStateOf(initialQuery) }
    var expandedIds by remember { mutableStateOf(emptySet<Long>()) }

    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(reload) {
        loading = true
        error = null
        treeUnavailable = false
        try {
            root = repo.listQuickGroup(ctx)
            treeLoaded = true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            val text = laximoUserMessage(e)
            // Каталог ответил ошибкой — повторять бесполезно, предложим все узлы; нет связи — «Повторить»
            if (e is LaximoApiException) treeUnavailable = true else error = text
        } finally {
            loading = false
        }
    }

    fun closeGroup() {
        selectedGroup = null
        categories = emptyList()
        error = null
    }
    // Системная «Назад» из деталей группы — к дереву групп, а не из подбора
    androidx.activity.compose.BackHandler(enabled = selectedGroup != null) { closeGroup() }

    fun openUnit(unit: LaximoUnit) {
        val i = Intent(context, UnitDetailsActivity::class.java).apply {
            putExtra("catalog", ctx.catalog)
            putExtra("vehicleId", ctx.vehicleId)
            putExtra("unitSsd", unit.ssd)
            putExtra("unitId", unit.unitId)
            putExtra("unitName", unit.name)
            putExtra("imageUrl", resolveLaximoImageUrl(unit.largeImageUrl ?: unit.imageUrl, LaximoImageSize.SOURCE))
            // Машина — по цепочке экранов: статическая CurrentCar после выгрузки процесса пустая
            putExtra("brand", brand)
            putExtra("name", carName)
        }
        context.startActivity(i)
    }

    // «Все узлы»: полный каталог машины по разделам
    fun openAllUnits() {
        val i = Intent(context, CatalogCategoriesActivity::class.java).apply {
            putExtra("catalog", ctx.catalog)
            putExtra("vehicleId", ctx.vehicleId)
            putExtra("ssd", ctx.ssd)
            putExtra("brand", brand)
            putExtra("name", carName)
        }
        context.startActivity(i)
    }

    fun openGroup(node: LaximoQuickGroupNode, gid: Long) {
        if (groupJob?.isActive == true) return
        loading = true
        error = null
        groupJob = scope.launch {
            try {
                categories = repo.listQuickDetail(ctx, gid, all = true)
                selectedGroup = node
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = laximoUserMessage(e)
            } finally {
                loading = false
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(selectedGroup?.name ?: "Подбор запчастей", maxLines = 1)
                        if (car.isNotBlank()) Text(car, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                    }
                },
                actions = {
                    if (selectedGroup == null) {
                        TextButton(onClick = { openAllUnits() }) { Text("Все узлы") }
                    }
                },
                navigationIcon = {
                    // Без выбранного раздела — обычная стрелка (с 10.11 подбор открывается сразу, минуя карточку машины)
                    if (selectedGroup == null) com.example.myapplication.ui.BackButton()
                    // Из раздела — та же стрелка, но назад к списку разделов
                    if (selectedGroup != null) {
                        IconButton(onClick = { closeGroup() }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад")
                        }
                    }
                }
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())

            if (selectedGroup == null) {
                // Быстрого подбора для машины нет: каталог так ответил или прислал пустое дерево
                val noTree = treeUnavailable || (treeLoaded && root?.children.isNullOrEmpty())

                if (!noTree) OutlinedTextField(
                    value = searchQuery,
                    onValueChange = { searchQuery = it },
                    label = { Text("Что ищем? Например, колодки") },
                    leadingIcon = { Icon(Icons.Default.Search, null) },
                    trailingIcon = {
                        if (searchQuery.isNotEmpty()) IconButton(onClick = { searchQuery = "" }) { Icon(Icons.Default.Close, "Очистить") }
                    },
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    singleLine = true
                )

                if (error != null) {
                    com.example.myapplication.ui.ErrorState(error!!, onRetry = if (root == null) { { reload += 1 } } else null)
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

                if (noTree) {
                    com.example.myapplication.ui.EmptyState(
                        "Для этой машины быстрый подбор недоступен",
                        action = "Открыть все узлы",
                        onAction = { openAllUnits() }
                    )
                } else if (treeLoaded && displayItems.isEmpty() && searchQuery.isNotBlank()) {
                    com.example.myapplication.ui.EmptyState(
                        "Ничего не нашлось. Попробуйте другое слово или откройте все узлы",
                        action = "Открыть все узлы",
                        onAction = { openAllUnits() }
                    )
                } else LazyColumn(Modifier.fillMaxSize()) {
                    itemsIndexed(displayItems, key = { index, item ->
                        // Индекс в списке гарантирует уникальность, даже если один и тот же
                        // узел встречается в дереве под разными родителями (частый случай
                        // при поиске: "Фильтры"/"Колодки" есть в нескольких категориях сразу).
                        "${index}_${item.node.quickGroupId}_${item.node.name.hashCode()}"
                    }) { _, hit ->
                        val node = hit.node
                        val level = hit.level
                        val isGroup = node.children.isNotEmpty() && searchQuery.isEmpty()
                        val open = node.quickGroupId?.let { expandedIds.contains(it) } == true
                        ListItem(
                            headlineContent = { Text(node.name, fontWeight = if (level == 0) FontWeight.SemiBold else null) },
                            // Группа раскрывается (▾/▸), конечный пункт ведёт к деталям (›)
                            trailingContent = { Text(if (isGroup) (if (open) "▾" else "▸") else "›", style = MaterialTheme.typography.titleMedium) },
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
                                        openGroup(node, gid)
                                    }
                                }
                                .padding(start = (level * 16).dp)
                        )
                        HorizontalDivider()
                    }
                }
            } else {
                if (error != null) {
                    com.example.myapplication.ui.ErrorState(error!!)
                }
                if (categories.all { it.units.isEmpty() } && error == null) {
                    com.example.myapplication.ui.EmptyState("В этой группе для вашей машины деталей не нашлось. Попробуйте «Все узлы» или спросите менеджера в чате.")
                }

                LazyColumn(Modifier.fillMaxSize()) {
                    // Один и тот же узел бывает в нескольких категориях — ключ включает номер категории,
                    // иначе список падает на одинаковых ключах
                    categories.forEachIndexed { ci, cat ->
                        item(key = "header_${ci}_${cat.name}") {
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
                        itemsIndexed(cat.units, key = { ui, u -> "unit_${ci}_${ui}_${u.unitId}" }) { _, unit ->
                            ListItem(
                                headlineContent = { Text(unit.name) },
                                supportingContent = { unit.code?.let { Text("Код: $it") } },
                                trailingContent = { Text("Схема ›", color = MaterialTheme.colorScheme.primary) },
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
