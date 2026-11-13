package com.example.myapplication
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Close
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import com.example.myapplication.ui.ErrorState
import com.example.myapplication.ui.WaitingState
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.AbcpShop
import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.CartIconButton
import com.example.myapplication.abcp.OffersActivity
import com.example.myapplication.abcp.autoPickBrand
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Поиск по артикулу: номер → список брендов → карточка с ценами. */
@OptIn(ExperimentalMaterial3Api::class)
class SearchActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefillNumber = intent.getStringExtra(EXTRA_NUMBER).orEmpty()
        val preferredBrand = intent.getStringExtra(EXTRA_BRAND)
        setContent { AvtodrugTheme { SearchScreen(prefillNumber, preferredBrand) } }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(prefillNumber: String = "", preferredBrand: String? = null) {
    val ctx = LocalContext.current
    val shop = remember { AbcpShop(SessionManager(ctx)) }
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf(prefillNumber) }
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    var tips by remember { mutableStateOf<List<BrandHit>>(emptyList()) }
    var brands by remember { mutableStateOf<List<BrandHit>?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var history by remember { mutableStateOf<List<BrandHit>>(emptyList()) }
    // Ищем бренд, который дают большинство поставщиков; клиент нажал бренд сам — отменяем
    var picking by remember { mutableStateOf<kotlinx.coroutines.Job?>(null) }
    // У скольких поставщиков проверенные бренды — подпись в списке и порядок (больше поставщиков — выше)
    var suppliers by remember { mutableStateOf<Map<String, Int>>(emptyMap()) }
    fun stopPicking() { picking?.cancel(); picking = null }
    // Идущий поиск: новый запрос, правка номера и ✕ его отменяют — иначе поздний ответ по старому номеру
    // затирал список и сам открывал выдачу не по тому номеру (аудит 13.11)
    var searching by remember { mutableStateOf<kotlinx.coroutines.Job?>(null) }
    fun stopSearch() { searching?.cancel(); searching = null; loading = false }
    // Сами открываем выдачу, только пока человек на этом экране: ушёл в корзину — поверх ничего не выскакивает
    val lifecycle = androidx.compose.ui.platform.LocalLifecycleOwner.current.lifecycle
    fun onScreen() = lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.RESUMED)

    LaunchedEffect(Unit) { if (!shop.isGuest) history = runCatching { shop.history() }.getOrDefault(emptyList()).take(20) }

    // «Фото упаковки»: штрихкоды и напечатанный текст → варианты артикула на выбор
    var scanMenu by remember { mutableStateOf(false) }
    var scanning by remember { mutableStateOf(false) }
    var candidates by remember { mutableStateOf<List<String>?>(null) }
    val photoUri = remember {
        val f = java.io.File(ctx.cacheDir, "scan/label.jpg").apply { parentFile?.mkdirs() }
        androidx.core.content.FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)
    }
    fun recognizeLabel(uri: android.net.Uri) {
        scanning = true
        error = null
        scope.launch {
            try {
                val text = recognizeText(ctx, uri)
                val found = extractArticleCandidates(text, scanBarcodes(ctx, uri))
                com.example.myapplication.Analytics.event("label_scan", mapOf("found" to found.size))
                if (found.isEmpty()) error = "На фото не нашлось номера. Снимите этикетку ближе и ровнее, при хорошем свете."
                else candidates = found
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Поиск → фото упаковки", e)
                error = "Не удалось распознать фото. Попробуйте ещё раз."
            } finally {
                scanning = false
            }
        }
    }
    val takePhoto = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.TakePicture()
    ) { ok -> if (ok) recognizeLabel(photoUri) }
    val pickPhoto = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.GetContent()
    ) { uri -> uri?.let { recognizeLabel(it) } }

    fun search() {
        val n = query.trim()
        if (n.isBlank()) return
        stopPicking()
        stopSearch()
        keyboard?.hide()
        focus.clearFocus()
        tips = emptyList()
        loading = true
        error = null
        searching = scope.launch {
            val me = coroutineContext[kotlinx.coroutines.Job]
            try {
                // Бренды в наличии — наверху списка
                val found = shop.brands(n).sortedByDescending { it.available }
                brands = found
                suppliers = emptyMap()
                // Выбор бренда пропускаем, когда он однозначен (один, известен заранее, единственный в наличии)
                com.example.myapplication.Analytics.event("search", mapOf("number" to n, "found" to found.size, "guest" to shop.isGuest))
                // Что ищут, а у нас нет — список для закупки
                if (found.isEmpty()) com.example.myapplication.Analytics.event("search_empty", mapOf("number" to n))
                val auto = autoPickBrand(found, preferredBrand)
                if (auto != null) { if (onScreen()) openOffers(ctx, auto) }
                else if (found.size > 1) picking = scope.launch {
                    try {
                        val votes = shop.dominantBrand(found)
                        suppliers = votes.suppliers
                        val best = votes.pick
                        // Клиент успел нажать бренд сам — ничего не открываем поверх
                        if (best != null && isActive && onScreen()) {
                            com.example.myapplication.Analytics.event("brand_autopick", mapOf("brand" to best.brand))
                            openOffers(ctx, best, picked = true)
                        }
                    } finally { if (picking == coroutineContext[kotlinx.coroutines.Job]) picking = null }
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e // отменили сами (новый поиск, ✕, уход с экрана) — не ошибка
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Поиск → бренды", e)
                error = e.message ?: "Не удалось выполнить поиск. Проверьте интернет."
            } finally {
                // Только свой: отменённый поиск не должен гасить индикатор нового
                if (searching == me) { searching = null; loading = false }
            }
        }
    }

    // Один раз: система выгрузила приложение в фоне (бюджетные телефоны) — при возврате «назад» с выдачи экран
    // создаётся заново и без этой отметки снова искал бы и снова бросал в ту же выдачу
    var autoSearched by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(Unit) { if (prefillNumber.isNotBlank() && !autoSearched) { autoSearched = true; search() } }

    // Подсказки при вводе (search/tips) отключены: на тарифе ABCP их 100 в сутки на весь магазин —
    // сгорели бы за полчаса. Вместо них — «Вы искали» и выбор бренда (search/brands, 150 тыс. в сутки).

    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = { com.example.myapplication.ui.BackButton() },
                title = { Text("Поиск по артикулу") },
                actions = { if (!shop.isGuest) CartIconButton() }
            )
        }
    ) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it; brands = null; error = null; stopPicking(); stopSearch() },
                label = { Text("Артикул или OEM-номер") },
                singleLine = true,
                leadingIcon = { Icon(Icons.Default.Search, null) },
                trailingIcon = {
                    if (query.isNotEmpty()) IconButton(onClick = { query = ""; brands = null; error = null; stopPicking(); stopSearch() }) {
                        Icon(Icons.Default.Close, "Очистить")
                    } else Box {
                        IconButton(onClick = { scanMenu = true }, enabled = !scanning) {
                            Text(if (scanning) "…" else "📷", style = MaterialTheme.typography.titleLarge)
                        }
                        DropdownMenu(expanded = scanMenu, onDismissRequest = { scanMenu = false }) {
                            DropdownMenuItem(text = { Text("Сфотографировать упаковку") }, onClick = { scanMenu = false; takePhoto.launch(photoUri) })
                            DropdownMenuItem(text = { Text("Выбрать фото из галереи") }, onClick = { scanMenu = false; pickPhoto.launch("image/*") })
                        }
                    }
                },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { search() }),
                modifier = Modifier.fillMaxWidth()
            )

            // Клавишу «Поиск» на клавиатуре находят не все (особенно после вставки номера из буфера)
            if (query.isNotBlank() && brands == null && !loading && error == null) {
                Button(onClick = { search() }, modifier = Modifier.fillMaxWidth()) { Text("Найти") }
            }

            if (query.isBlank()) OneTimeHint("label_photo", "Нет номера под рукой? Нажмите 📷 и сфотографируйте упаковку детали — найдём по штрихкоду или надписи.")
            candidates?.let { list ->
                AlertDialog(
                    onDismissRequest = { candidates = null },
                    title = { Text("Какой номер искать?") },
                    text = {
                        Column {
                            list.forEach { c ->
                                TextButton(onClick = { candidates = null; query = c.replace(" ", ""); search() }) { Text(c) }
                            }
                        }
                    },
                    confirmButton = {},
                    dismissButton = { TextButton(onClick = { candidates = null }) { Text("Отмена") } }
                )
            }

            when {
                loading -> WaitingState(first = "Ищем номер…", onCancel = { stopSearch() })
                error != null -> ErrorState(error!!, onRetry = { search() })
                brands != null && brands!!.isEmpty() -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("По номеру «${query.trim()}» ничего не найдено. Проверьте номер или спросите менеджера — подберём.")
                    // Тупик без кнопки: чат был только на главной (телефон 10.11)
                    Button(onClick = { ctx.startActivity(Intent(ctx, ChatActivity::class.java)) }) { Text("Спросить менеджера") }
                }
                brands != null -> {
                    if (picking != null) {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                        Text("Смотрим, какой производитель чаще у поставщиков…", style = MaterialTheme.typography.bodySmall)
                    }
                    Text("Выберите производителя", style = MaterialTheme.typography.titleSmall)
                    // Проверенные бренды — наверх по числу поставщиков (самый ходовой первым), остальные — как пришли
                    val ordered = brands!!.withIndex().sortedWith(
                        compareByDescending<IndexedValue<BrandHit>> { suppliers[it.value.brand] ?: -1 }.thenBy { it.index }
                    ).map { it.value }
                    HitList(ordered, suppliers) { stopPicking(); openOffers(ctx, it) }
                }
                tips.isNotEmpty() -> HitList(tips) { openOffers(ctx, it) }
                query.isBlank() && history.isNotEmpty() -> {
                    Text("Вы искали", style = MaterialTheme.typography.titleSmall)
                    HitList(history) { openOffers(ctx, it) }
                }
            }
        }
    }
}

/** picked — бренд выбран сам из нескольких: на выдаче будет «Не тот? Выбрать другой». */
private fun openOffers(ctx: Context, hit: BrandHit, picked: Boolean = false) {
    ctx.startActivity(
        Intent(ctx, OffersActivity::class.java)
            .putExtra(OffersActivity.EXTRA_BRAND, hit.brand)
            .putExtra(OffersActivity.EXTRA_NUMBER, hit.number)
            .putExtra(OffersActivity.EXTRA_DESCRIPTION, hit.description)
            .putExtra(OffersActivity.EXTRA_AUTOPICKED, picked)
    )
}

@Composable
private fun HitList(hits: List<BrandHit>, suppliers: Map<String, Int> = emptyMap(), onClick: (BrandHit) -> Unit) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items(hits) { h ->
            ElevatedCard(Modifier.fillMaxWidth().clickable { onClick(h) }) {
                Column(Modifier.padding(12.dp)) {
                    // «в наличии» ABCP ставит почти всем брендам номера — не подпись, а шум (10.11). Вместо неё — у скольких
                    // поставщиков есть именно этот бренд и номер (известно для проверенных автовыбором)
                    Text("${h.brand}  ${h.number}", style = MaterialTheme.typography.titleSmall)
                    suppliers[h.brand]?.let { n ->
                        Text(
                            if (n == 0) "нет предложений" else "у $n ${suppliersWord(n)}",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (n == 0) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary
                        )
                    }
                    if (h.description.isNotBlank()) {
                        Text(h.description, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

/** 1 поставщика, 2 поставщиков… — «у N поставщиков» читается одинаково при любом N, кроме 1 */
private fun suppliersWord(n: Int) = if (n % 10 == 1 && n % 100 != 11) "поставщика" else "поставщиков"
