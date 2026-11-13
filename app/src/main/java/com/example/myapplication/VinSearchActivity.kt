package com.example.myapplication
import com.example.myapplication.ui.theme.AvtodrugTheme

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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.LaximoClient
import com.example.myapplication.laximo.LaximoRepository
import com.example.myapplication.laximo.QuickGroupsActivity
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
class VinSearchActivity : ComponentActivity() {

    private val repo: LaximoRepository by lazy {
        LaximoRepository(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefillVin = intent.getStringExtra("prefillVin").orEmpty()
        // С главной («Моя машина» → «Фильтры»): сразу открыть подбор с этим разделом
        val quickQuery = intent.getStringExtra("quickQuery").orEmpty()

        setContent {
            AvtodrugTheme {
                val ctx = LocalContext.current
                val scope = rememberCoroutineScope()

                var vinText by remember { mutableStateOf(prefillVin) }
                var loading by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf<String?>(null) }
                var results by remember { mutableStateOf<List<LaximoVehicleContext>>(emptyList()) }
                var searched by remember { mutableStateOf(false) } // «Ничего не найдено» — только после поиска
                val guest = remember { !SessionManager(ctx).isLoggedIn() }
                val keyboard = androidx.compose.ui.platform.LocalSoftwareKeyboardController.current
                // Что уже в гараже — чтобы не предлагать «В мой гараж» повторно
                var garageKeys by remember { mutableStateOf(com.example.myapplication.abcp.GarageCache.peek(ctx).orEmpty().map { com.example.myapplication.abcp.garageKey(it.vin) }.toSet()) }
                LaunchedEffect(Unit) {
                    if (!guest) runCatching { loadGarage(ctx) }.getOrNull()?.let { list ->
                        garageKeys = list.map { com.example.myapplication.abcp.garageKey(it.vin) }.toSet()
                    }
                }

                // Что уже искали: кнопка «Найти» нужна, только пока номер новый (машина найдена — она лишняя и путает)
                var searchedFor by remember { mutableStateOf("") }
                // Идущий поиск: новый запуск отменяет прежний — иначе открываются два экрана подбора
                var searchJob by remember { mutableStateOf<Job?>(null) }
                var searchingFor by remember { mutableStateOf("") }
                val searchRun = remember { intArrayOf(0) } // номер запуска: отменённый поиск не трогает состояние нового
                // «В мой гараж» нажали — до ответа кнопка не работает (двойное нажатие создавало две машины)
                var addingToGarage by remember { mutableStateOf(false) }
                fun inGarage(q: String) = com.example.myapplication.abcp.garageKey(
                    com.example.myapplication.laximo.normalizeRuPlate(q) ?: q
                ) in garageKeys

                fun onSearchClick() {
                    if (vinText.isBlank()) {
                        error = "Введите VIN, номер кузова или госномер"
                        return
                    }
                    // Единый вид: без пробелов, заглавными, русские буквы-двойники заменены латинскими
                    val query = com.example.myapplication.laximo.normalizeVehicleQuery(vinText)
                    val vin = query.value
                    // Этот же номер уже ищем («Поиск» на клавиатуре, повтор после фото) — второй раз не запускаем
                    if (loading && vin == searchingFor) return

                    searchJob?.cancel()
                    val runNo = ++searchRun[0]
                    keyboard?.hide()
                    results = emptyList()
                    if (query.hint != null) {
                        // В VIN не 17 знаков — запрос не отправляем, сразу подсказка
                        loading = false
                        searched = false
                        error = query.hint
                        return
                    }
                    loading = true
                    error = null
                    searched = true
                    searchingFor = vin

                    searchJob = scope.launch {
                        try {
                            val list = withContext(Dispatchers.IO) {
                                repo.findVehicleAny(vin)
                            }
                            results = list
                            searchedFor = vin
                            // Машина нашлась одна — сразу к подбору. Её ещё нет в гараже — карточка остаётся позади
                            // («назад» — к ней, там «В мой гараж»); уже в гараже — карточка не нужна, «назад» — откуда пришли
                            if (list.size == 1) {
                                val r = list.first()
                                startActivity(
                                    Intent(ctx, QuickGroupsActivity::class.java)
                                        .putExtra("catalog", r.catalog).putExtra("vehicleId", r.vehicleId).putExtra("ssd", r.ssd)
                                        .putExtra("brand", r.brand ?: "").putExtra("name", r.name ?: "")
                                        .apply { if (quickQuery.isNotBlank()) putExtra("query", quickQuery) }
                                )
                                if (quickQuery.isNotBlank() || (!guest && inGarage(vin))) finish()
                            }
                            com.example.myapplication.Analytics.event("vehicle_search", mapOf(
                                "kind" to when {
                                    com.example.myapplication.laximo.normalizeRuPlate(vin) != null -> "plate"
                                    looksLikeVin(vin) -> "vin"
                                    else -> "frame"
                                },
                                "found" to list.size
                            ))
                        } catch (e: kotlinx.coroutines.CancellationException) {
                            throw e // поиск отменён новым запуском — это не ошибка
                        } catch (e: Exception) {
                            com.example.myapplication.Analytics.error("Подбор → поиск авто", e)
                            Log.e("VIN_SEARCH", "EX", e)
                            error = com.example.myapplication.laximo.laximoUserMessage(e)
                        } finally {
                            if (runNo == searchRun[0]) loading = false
                        }
                    }
                }

                var scanMenu by remember { mutableStateOf(false) }
                var scanning by remember { mutableStateOf(false) }
                var scanChoices by remember { mutableStateOf<List<VehicleCode>?>(null) }
                val photoUri = remember {
                    val f = java.io.File(ctx.cacheDir, "scan/photo.jpg").apply { parentFile?.mkdirs() }
                    androidx.core.content.FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)
                }

                fun recognize(uri: android.net.Uri) {
                    scanning = true
                    error = null
                    scope.launch {
                        try {
                            val codes = extractVehicleCodes(recognizeText(ctx, uri))
                            com.example.myapplication.Analytics.event("photo_scan", mapOf("codes" to codes.size))
                            when (codes.size) {
                                0 -> error = "На фото не нашлось VIN или номера. Попробуйте снять ближе и ровнее."
                                1 -> { vinText = codes[0].value; onSearchClick() }
                                else -> scanChoices = codes
                            }
                        } catch (e: kotlinx.coroutines.CancellationException) {
                            throw e
                        } catch (e: Throwable) {
                            // Throwable: нехватка памяти на огромном снимке — не Exception
                            com.example.myapplication.Analytics.error("Подбор → распознавание фото", e)
                            error = "Не удалось распознать фото. Попробуйте снять ещё раз или введите номер вручную."
                        } finally {
                            scanning = false
                        }
                    }
                }

                val takePhoto = androidx.activity.compose.rememberLauncherForActivityResult(
                    androidx.activity.result.contract.ActivityResultContracts.TakePicture()
                ) { ok -> if (ok) recognize(photoUri) }
                val pickPhoto = androidx.activity.compose.rememberLauncherForActivityResult(
                    androidx.activity.result.contract.ActivityResultContracts.GetContent()
                ) { uri -> uri?.let { recognize(it) } }

                scanChoices?.let { list ->
                    AlertDialog(
                        onDismissRequest = { scanChoices = null },
                        title = { Text("Что искать?") },
                        text = {
                            Column {
                                list.forEach { c ->
                                    TextButton(onClick = { scanChoices = null; vinText = c.value; onSearchClick() }) {
                                        Text("${c.title}: ${c.value}")
                                    }
                                }
                            }
                        },
                        confirmButton = {},
                        dismissButton = { TextButton(onClick = { scanChoices = null }) { Text("Отмена") } }
                    )
                }

                LaunchedEffect(prefillVin) {
                    if (prefillVin.isNotBlank()) {
                        onSearchClick()
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Поиск автомобиля") }) }
                ) { padding ->
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(padding)
                            .padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        OutlinedTextField(
                            value = vinText,
                            onValueChange = {
                                if (it != vinText) {
                                    // Номер изменили — прежние результаты и идущий поиск к нему уже не относятся
                                    searchJob?.cancel()
                                    searchRun[0]++
                                    loading = false
                                    results = emptyList()
                                    searched = false
                                    error = null
                                }
                                vinText = it
                            },
                            label = { Text("VIN, номер кузова или госномер") },
                            placeholder = { Text("XTA21099… / SGL5-400683 / А123ВС92") },
                            singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                imeAction = androidx.compose.ui.text.input.ImeAction.Search,
                                capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Characters
                            ),
                            keyboardActions = androidx.compose.foundation.text.KeyboardActions(onSearch = { onSearchClick() }),
                            trailingIcon = {
                                Box {
                                    IconButton(onClick = { scanMenu = true }, enabled = !scanning) {
                                        Text(if (scanning) "…" else "📷", style = MaterialTheme.typography.titleLarge)
                                    }
                                    DropdownMenu(expanded = scanMenu, onDismissRequest = { scanMenu = false }) {
                                        DropdownMenuItem(
                                            text = { Text("Сфотографировать VIN, СТС или номер") },
                                            onClick = {
                                                scanMenu = false
                                                // Приложения камеры на телефоне может не быть — launch тогда бросает исключение
                                                try {
                                                    takePhoto.launch(photoUri)
                                                } catch (e: android.content.ActivityNotFoundException) {
                                                    error = "Не нашлось приложения камеры. Выберите фото из галереи или введите номер вручную."
                                                }
                                            }
                                        )
                                        DropdownMenuItem(
                                            text = { Text("Выбрать фото из галереи") },
                                            onClick = {
                                                scanMenu = false
                                                try {
                                                    pickPhoto.launch("image/*")
                                                } catch (e: android.content.ActivityNotFoundException) {
                                                    error = "Не нашлось приложения для выбора фото. Введите номер вручную."
                                                }
                                            }
                                        )
                                    }
                                }
                            },
                            modifier = Modifier.fillMaxWidth()
                        )
                        // Одна подсказка про фото (раньше была ещё разовая карточка с тем же текстом)
                        Text(
                            "Нажмите 📷 в поле и сфотографируйте табличку VIN, СТС или номер машины — распознаем сами",
                            style = MaterialTheme.typography.bodySmall
                        )

                        if (loading || results.isEmpty()) Button(
                            onClick = { onSearchClick() },
                            enabled = !loading,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(if (loading) "Поиск..." else "Найти")
                        }

                        if (error != null) {
                            Text(
                                text = error!!,
                                color = MaterialTheme.colorScheme.error
                            )
                        }

                        if (loading) {
                            Box(Modifier.fillMaxWidth()) {
                                CircularProgressIndicator(Modifier.align(Alignment.Center))
                            }
                        }

                        if (!loading && error == null && searched) {
                            if (results.isEmpty()) {
                                Text("Машина не найдена. Проверьте VIN или номер — или спросите менеджера в чате, подберём вручную.")
                            } else {
                                LazyColumn(
                                    modifier = Modifier.fillMaxSize(),
                                    verticalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    items(results) { r ->
                                        ElevatedCard(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .clickable {
                                                    // Сразу открываем Быстрый подбор (QuickGroupsActivity)
                                                    val i = Intent(ctx, QuickGroupsActivity::class.java)
                                                    i.putExtra("catalog", r.catalog)
                                                    i.putExtra("vehicleId", r.vehicleId)
                                                    i.putExtra("ssd", r.ssd)
                                                    i.putExtra("brand", r.brand ?: "")
                                                    i.putExtra("name", r.name ?: "")
                                                    startActivity(i)
                                                }
                                        ) {
                                            Column(Modifier.padding(12.dp)) {
                                                Text(
                                                    text = "${r.brand ?: ""} ${r.name ?: ""}".trim().ifBlank { r.catalog },
                                                    style = MaterialTheme.typography.titleMedium
                                                )
                                                Spacer(Modifier.height(4.dp))
                                                // Номер, по которому машина найдена, — а не то, что сейчас в поле
                                                Text("Запрос: $searchedFor", style = MaterialTheme.typography.bodySmall)
                                                val carInGarage = inGarage(searchedFor)
                                                if (!guest && carInGarage) Text("✓ В гараже", style = MaterialTheme.typography.labelLarge,
                                                    color = com.example.myapplication.ui.theme.DeliveryColors.today, modifier = Modifier.padding(vertical = 8.dp))
                                                if (!guest && !carInGarage) TextButton(
                                                    contentPadding = PaddingValues(0.dp),
                                                    enabled = !addingToGarage,
                                                    onClick = {
                                                        if (!addingToGarage) {
                                                        addingToGarage = true
                                                        val q = searchedFor
                                                        val kind = when {
                                                            com.example.myapplication.laximo.normalizeRuPlate(q) != null -> "plate"
                                                            looksLikeVin(q) -> "vin"
                                                            else -> "frame"
                                                        }
                                                        val value = com.example.myapplication.laximo.normalizeRuPlate(q) ?: q.uppercase()
                                                        scope.launch {
                                                            try {
                                                                com.example.myapplication.abcp.AbcpShop(SessionManager(ctx))
                                                                    .addToGarage("${r.brand.orEmpty()} ${r.name.orEmpty()}".trim(), value, kind)
                                                                com.example.myapplication.Analytics.event("garage_add", mapOf("kind" to kind))
                                                                garageKeys = garageKeys + com.example.myapplication.abcp.garageKey(value)
                                                                // Гараж изменился — главная и вкладка «Гараж» перечитают его один раз
                                                                com.example.myapplication.abcp.GarageCache.invalidate(ctx)
                                                                android.widget.Toast.makeText(ctx, "Машина добавлена в гараж", android.widget.Toast.LENGTH_SHORT).show()
                                                            } catch (e: kotlinx.coroutines.CancellationException) {
                                                                throw e
                                                            } catch (e: Exception) {
                                                                com.example.myapplication.Analytics.error("Подбор → в гараж", e)
                                                                android.widget.Toast.makeText(ctx, e.message ?: "Не удалось добавить", android.widget.Toast.LENGTH_LONG).show()
                                                            } finally {
                                                                addingToGarage = false
                                                            }
                                                        }
                                                        }
                                                    }
                                                ) { Text("＋ В мой гараж") }
                                                if (r.attributes.isNotEmpty()) {
                                                    Spacer(Modifier.height(6.dp))
                                                    // Первые характеристики сразу, остальные — по нажатию: иначе карточка на весь экран
                                                    var more by remember { mutableStateOf(false) }
                                                    val attrs = r.attributes.filter { !it.value.isNullOrBlank() }
                                                    (if (more) attrs else attrs.take(4)).forEach { attr ->
                                                        if (!attr.value.isNullOrBlank()) {
                                                            Text(
                                                                "${attr.name ?: attr.key}: ${attr.value}",
                                                                style = MaterialTheme.typography.bodySmall
                                                            )
                                                        }
                                                    }
                                                    if (attrs.size > 4) TextButton(onClick = { more = !more }, contentPadding = PaddingValues(0.dp)) {
                                                        Text(if (more) "Свернуть" else "Все характеристики (${attrs.size})")
                                                    }
                                                }
                                                // Явная кнопка: по карточке не видно, что на неё можно нажать (телефон 10.11)
                                                Spacer(Modifier.height(8.dp))
                                                Button(
                                                    onClick = {
                                                        startActivity(
                                                            Intent(ctx, QuickGroupsActivity::class.java)
                                                                .putExtra("catalog", r.catalog).putExtra("vehicleId", r.vehicleId).putExtra("ssd", r.ssd)
                                                                .putExtra("brand", r.brand ?: "").putExtra("name", r.name ?: "")
                                                        )
                                                    },
                                                    modifier = Modifier.fillMaxWidth()
                                                ) { Text("Подобрать запчасти →") }
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


/** Текст с фото через ML Kit (модель встроена в приложение — работает без интернета и Google-сервисов). */
suspend fun recognizeText(ctx: android.content.Context, uri: android.net.Uri): String {
    // Уменьшенный снимок, декодирование не на главном потоке (см. loadScanPhoto в ScanCodes.kt)
    val photo = loadScanPhoto(ctx, uri)
    return kotlinx.coroutines.suspendCancellableCoroutine { cont ->
        val image = com.google.mlkit.vision.common.InputImage.fromBitmap(photo.bitmap, photo.rotation)
        val client = com.google.mlkit.vision.text.TextRecognition.getClient(
            com.google.mlkit.vision.text.latin.TextRecognizerOptions.DEFAULT_OPTIONS
        )
        client.process(image)
            .addOnSuccessListener { cont.resumeWith(Result.success(it.text)); client.close() }
            .addOnFailureListener { cont.resumeWith(Result.failure(it)); client.close() }
    }
}
