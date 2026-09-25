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

                fun onSearchClick() {
                    val vin = vinText.trim()
                    if (vin.isBlank()) {
                        error = "Введите VIN, номер кузова или госномер"
                        return
                    }

                    keyboard?.hide()
                    loading = true
                    error = null
                    results = emptyList()
                    searched = true

                    scope.launch {
                        try {
                            val list = withContext(Dispatchers.IO) {
                                repo.findVehicleAny(vin)
                            }
                            results = list
                        } catch (e: Exception) {
                            Log.e("VIN_SEARCH", "EX", e)
                            error = com.example.myapplication.laximo.laximoUserMessage(e)
                        } finally {
                            loading = false
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
                            when (codes.size) {
                                0 -> error = "На фото не нашлось VIN или номера. Попробуйте снять ближе и ровнее."
                                1 -> { vinText = codes[0].value; onSearchClick() }
                                else -> scanChoices = codes
                            }
                        } catch (e: Exception) {
                            error = "Не удалось распознать фото: ${e.message ?: e.javaClass.simpleName}"
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
                    topBar = { TopAppBar(title = { Text("Поиск автомобиля") }) }
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
                            onValueChange = { vinText = it },
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
                                            onClick = { scanMenu = false; takePhoto.launch(photoUri) }
                                        )
                                        DropdownMenuItem(
                                            text = { Text("Выбрать фото из галереи") },
                                            onClick = { scanMenu = false; pickPhoto.launch("image/*") }
                                        )
                                    }
                                }
                            },
                            modifier = Modifier.fillMaxWidth()
                        )
                        Text(
                            "Можно сфотографировать табличку VIN, СТС или номер машины — распознаем сами",
                            style = MaterialTheme.typography.bodySmall
                        )

                        Button(
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
                                                Text("Запрос: ${vinText.trim()}", style = MaterialTheme.typography.bodySmall)
                                                if (!guest) TextButton(
                                                    contentPadding = PaddingValues(0.dp),
                                                    onClick = {
                                                        val q = vinText.trim()
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
                                                                android.widget.Toast.makeText(ctx, "Машина добавлена в гараж", android.widget.Toast.LENGTH_SHORT).show()
                                                            } catch (e: Exception) {
                                                                android.widget.Toast.makeText(ctx, e.message ?: "Не удалось добавить", android.widget.Toast.LENGTH_LONG).show()
                                                            }
                                                        }
                                                    }
                                                ) { Text("＋ В мой гараж") }
                                                Text("Каталог: ${r.catalog}", style = MaterialTheme.typography.bodySmall)
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
suspend fun recognizeText(ctx: android.content.Context, uri: android.net.Uri): String =
    kotlinx.coroutines.suspendCancellableCoroutine { cont ->
        val image = com.google.mlkit.vision.common.InputImage.fromFilePath(ctx, uri)
        val client = com.google.mlkit.vision.text.TextRecognition.getClient(
            com.google.mlkit.vision.text.latin.TextRecognizerOptions.DEFAULT_OPTIONS
        )
        client.process(image)
            .addOnSuccessListener { cont.resumeWith(Result.success(it.text)); client.close() }
            .addOnFailureListener { cont.resumeWith(Result.failure(it)); client.close() }
    }
