package com.example.myapplication.laximo
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.myapplication.SearchActivity
import com.example.myapplication.laximo.model.LaximoDetail
import com.example.myapplication.laximo.model.LaximoImageMapItem
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class UnitDetailsActivity : ComponentActivity() {

    private val repo by lazy { LaximoRepository(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog") ?: ""
        val vehicleId = intent.getStringExtra("vehicleId") ?: "0"
        val unitId = intent.getStringExtra("unitId") ?: ""
        val unitName = intent.getStringExtra("unitName") ?: ""
        val unitSsd = intent.getStringExtra("unitSsd") ?: ""
        val imageUrl = intent.getStringExtra("imageUrl")
        // Марка машины — из Intent: статическая CurrentCar после выгрузки процесса пустая («Цены» открывались без бренда)
        val brand = intent.getStringExtra("brand").orEmpty().ifBlank { CurrentCar.brand }
        if (brand.isNotBlank()) CurrentCar.brand = brand
        val car = "${intent.getStringExtra("brand").orEmpty()} ${intent.getStringExtra("name").orEmpty()}".trim()

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = unitSsd)
        val unit = LaximoUnit(unitId = unitId, name = unitName, ssd = unitSsd, imageUrl = imageUrl)

        setContent {
            AvtodrugTheme {
                UnitDetailsScreen(ctx = ctx, unit = unit, repo = repo, brand = brand, car = car)
            }
        }
    }
}

/** Ключ строки «№ … на схеме» в списке: по нему проверяем, видны ли выбранные детали. */
private const val SELECTED_HEADER_KEY = "selected_header"


@Composable
private fun UnitDetailsScreen(
    ctx: LaximoVehicleContext,
    unit: LaximoUnit,
    repo: LaximoRepository,
    brand: String = "",
    car: String = ""
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var details by remember { mutableStateOf<List<LaximoDetail>>(emptyList()) }
    var mapItems by remember { mutableStateOf<List<LaximoImageMapItem>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var selectedCode by remember { mutableStateOf<String?>(null) }
    var isInteractingWithScheme by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(ctx.catalog, unit.unitId, unit.ssd, reload) {
        loading = true
        error = null
        runCatching {
            details = repo.listDetailByUnit(ctx, unit)
            // Разметка схемы не критична: без неё список деталей всё равно работает
            mapItems = runCatching { repo.listImageMapByUnit(ctx, unit) }.getOrDefault(emptyList())
        }.onFailure { e ->
            error = laximoUserMessage(e)
        }
        loading = false
    }

    val codes = remember(details) {
        details.mapNotNull { it.codeOnImage?.trim() }
            .filter { it.isNotBlank() }
            .distinct()
    }

    fun onDetailClick(detail: LaximoDetail) {
        detail.codeOnImage?.let { code ->
            if (code.isNotBlank()) {
                selectedCode = code
            }
        }
    }

    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    // Выбрали номер — выбранные детали встают сразу под схемой (она — первый элемент списка).
    // Детали и так видны — список не трогаем; иначе — к началу, чтобы схема осталась на экране.
    LaunchedEffect(selectedCode) {
        if (selectedCode.isNullOrBlank()) return@LaunchedEffect
        withFrameNanos { } // ждём кадр: список уже перестроен под выбор

        // Первая выбранная деталь (строка сразу за заголовком «№ … на схеме»), если она сейчас на экране
        fun firstSelected() = listState.layoutInfo.visibleItemsInfo.let { items ->
            items.firstOrNull { it.key == SELECTED_HEADER_KEY }?.let { h -> items.firstOrNull { it.index == h.index + 1 } }
        }
        fun overflow(): Int? = firstSelected()?.let { it.offset + it.size - listState.layoutInfo.viewportEndOffset }

        val before = overflow()
        if (before != null && before <= 0) return@LaunchedEffect // видна целиком
        if (listState.firstVisibleItemIndex != 0 || listState.firstVisibleItemScrollOffset != 0) {
            listState.animateScrollToItem(0)
            withFrameNanos { }
        }
        // Номер есть только на схеме, деталей с ним в списке нет — двигать дальше нечего
        if (details.none { it.codeOnImage == selectedCode }) return@LaunchedEffect
        // Небольшой экран: под схемой деталь не поместилась — сдвигаем, но не больше чем на 40% высоты схемы
        val scheme = listState.layoutInfo.visibleItemsInfo.firstOrNull { it.index == 0 } ?: return@LaunchedEffect
        val limit = scheme.size * 0.4f
        val need = overflow()
        if (need == null) listState.animateScrollBy(limit)
        else if (need > 0) listState.animateScrollBy(minOf(need.toFloat(), limit))
    }
    val selectedDetails = details.filter { !selectedCode.isNullOrBlank() && it.codeOnImage == selectedCode }
    val otherDetails = if (selectedDetails.isEmpty()) details else details - selectedDetails.toSet()
    var showOthers by remember(selectedCode) { mutableStateOf(false) }

    fun onCartClick(oem: String) {
        com.example.myapplication.Analytics.event("catalog_prices", mapOf("catalog" to ctx.catalog))
        // OEM со схемы → поиск ABCP: бренды → цены и аналоги
        context.startActivity(
            Intent(context, SearchActivity::class.java).apply {
                // Бренд оригинала по марке машины — поиск сразу откроет цены, без выбора бренда
                putExtra(SearchActivity.EXTRA_BRAND, brand.takeIf { it.isNotBlank() }?.let(::oemBrandFor).orEmpty())
                putExtra(SearchActivity.EXTRA_NUMBER, oem)
            }
        )
    }

    @OptIn(ExperimentalMaterial3Api::class)
    Scaffold(
        topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = {
            if (car.isBlank()) Text(unit.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
            else Column {
                // С подзаголовком в шапку помещается одна строка названия
                Text(unit.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(car, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }) }
    ) { pad ->
        // Адрес схемы; нет схемы — нет и подсказки про номера на ней
        val img = remember(unit) { resolveLaximoImageUrl(unit.largeImageUrl ?: unit.imageUrl, LaximoImageSize.SOURCE) }
        if (loading && details.isEmpty()) LinearProgressIndicator(Modifier.padding(pad).fillMaxWidth())
        LazyColumn(
            modifier = Modifier
                .padding(pad)
                .fillMaxSize(),
            userScrollEnabled = !isInteractingWithScheme,
            state = listState
        ) {

            item {
                if (img != null) {
                    Card(Modifier.padding(12.dp).fillMaxWidth()) {
                        UnitSchemeWithMap(
                            imageUrl = img,
                            mapItems = mapItems,
                            selectedCode = selectedCode,
                            showNumbers = true,
                            onSelectCode = { code ->
                                selectedCode = code
                            },
                            onInteractingChange = { isInteractingWithScheme = it }
                        )
                    }
                }
            }

            if (codes.isNotEmpty()) {
                item {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Номера на схеме",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(horizontal = 12.dp)
                    )
                    LazyRow(
                        modifier = Modifier.fillMaxWidth(),
                        contentPadding = PaddingValues(horizontal = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        items(codes) { code ->
                            FilterChip(
                                selected = selectedCode == code,
                                onClick = { selectedCode = code },
                                label = { Text(code) }
                            )
                        }
                    }
                }
            }

            if (!loading && error == null && details.isEmpty()) item {
                Text(
                    "В этом узле каталог не показал деталей. Напишите менеджеру — подберём.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 16.dp)
                )
            }
            if (!loading && error == null && details.isNotEmpty() && img != null) item {
                Text(
                    "Нажмите номер на схеме или деталь в списке, «Цены» — предложения магазина по этому номеру",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)
                )
            }
            if (selectedDetails.isNotEmpty()) {
                item(key = SELECTED_HEADER_KEY) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Text("№ $selectedCode на схеме", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                        TextButton(onClick = { selectedCode = null }) { Text("Сбросить") }
                    }
                }
                items(selectedDetails) { d ->
                    DetailCard(d = d, selected = true, onClick = { onDetailClick(d) }, onCartClick = { onCartClick(it) })
                }
                item {
                    TextButton(onClick = { showOthers = !showOthers }, modifier = Modifier.padding(horizontal = 4.dp)) {
                        Text(if (showOthers) "Скрыть остальные детали" else "Остальные детали узла (${otherDetails.size}) ▾")
                    }
                }
            }
            if (selectedDetails.isEmpty() || showOthers) {
                items(otherDetails) { d ->
                    DetailCard(d = d, selected = false, onClick = { onDetailClick(d) }, onCartClick = { onCartClick(it) })
                }
            }

            if (error != null) {
                item { com.example.myapplication.ui.ErrorState(error!!, onRetry = { reload++ }) }
            }
        }
    }
}
