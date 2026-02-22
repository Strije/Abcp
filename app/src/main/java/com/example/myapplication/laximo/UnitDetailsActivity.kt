package com.example.myapplication

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import coil.compose.SubcomposeAsyncImageContent
import com.example.myapplication.laximo.LaximoApiException
import com.example.myapplication.laximo.LaximoClient
import com.example.myapplication.laximo.LaximoRepository
import com.example.myapplication.laximo.laximoAuth
import com.example.myapplication.laximo.LaximoImageSize
import com.example.myapplication.laximo.resolveLaximoImage
import com.example.myapplication.laximo.model.*

@OptIn(ExperimentalMaterial3Api::class)
class UnitDetailsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val unitId = intent.getStringExtra("unitId").orEmpty()
        val unitName = intent.getStringExtra("unitName").orEmpty()
        val unitSsd = intent.getStringExtra("unitSsd").orEmpty()
        val imageUrl = intent.getStringExtra("imageUrl")

        val client = LaximoClient(
            username = BuildConfig.LAXIMO_USER,
            password = BuildConfig.LAXIMO_PASS,
            language = "ru_RU"
        )
        val repo = LaximoRepository(client)

        val ctx = LaximoVehicleContext(
            catalog = catalog,
            vehicleId = vehicleId,
            ssd = unitSsd
        )
        val unit = LaximoUnit(
            unitId = unitId,
            name = unitName,
            ssd = unitSsd,
            imageUrl = imageUrl
        )

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }

                var details by remember { mutableStateOf<List<LaximoDetail>>(emptyList()) }
                var mapItems by remember { mutableStateOf<List<LaximoImageMapItem>>(emptyList()) }

                var selectedCode by remember { mutableStateOf<String?>(null) }

                LaunchedEffect(unitId, unitSsd) {
                    loading = true
                    error = null
                    try {
                        val d = repo.listDetailByUnit(ctx, unit)
                        val m = repo.listImageMapByUnit(ctx, unit)
                        details = d
                        mapItems = m
                        Log.d("LAXIMO_UNIT", "details=${d.size} map=${m.size}")
                    } catch (e: LaximoApiException) {
                        error = e.pretty
                    } catch (e: Exception) {
                        Log.e("LAXIMO_UNIT", "EX ${e.message}", e)
                        error = "Ошибка загрузки узла: ${e.message ?: "unknown"}"
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text(unitName.ifBlank { "Узел" }) }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(16.dp))
                            else -> {
                                Column(Modifier.fillMaxSize()) {
                                    UnitImageWithMap(
                                        imageUrl = unit.imageUrl.resolveLaximoImage(LaximoImageSize.SOURCE),
                                        mapItems = mapItems,
                                        selectedCode = selectedCode,
                                        onSelectCode = { selectedCode = it }
                                    )

                                    Divider()

                                    LazyColumn(
                                        modifier = Modifier.fillMaxSize(),
                                        contentPadding = PaddingValues(12.dp),
                                        verticalArrangement = Arrangement.spacedBy(10.dp)
                                    ) {
                                        items(details) { d ->
                                            val code = d.codeOnImage
                                            val isSelected = (code != null && code == selectedCode)

                                            ElevatedCard(
                                                modifier = Modifier
                                                    .fillMaxWidth()
                                                    .clickable { selectedCode = code }
                                            ) {
                                                Column(Modifier.padding(12.dp)) {
                                                    Text(
                                                        text = d.name ?: "—",
                                                        style = MaterialTheme.typography.titleSmall
                                                    )
                                                    Spacer(Modifier.height(6.dp))
                                                    Text("OEM: ${d.oem ?: "—"}")
                                                    Text("Код на схеме: ${code ?: "—"}")

                                                    if (!d.filter.isNullOrBlank()) {
                                                        Text(
                                                            "Есть условие применимости (filter=${d.filter})",
                                                            color = MaterialTheme.colorScheme.tertiary
                                                        )
                                                    }

                                                    if (isSelected) {
                                                        Spacer(Modifier.height(6.dp))
                                                        Text(
                                                            "Выбрано на схеме",
                                                            color = MaterialTheme.colorScheme.primary
                                                        )
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

@Composable
private fun UnitImageWithMap(
    imageUrl: String?,
    mapItems: List<LaximoImageMapItem>,
    selectedCode: String?,
    onSelectCode: (String?) -> Unit
) {
    if (imageUrl.isNullOrBlank()) {
        Text("Нет изображения узла", Modifier.padding(12.dp))
        return
    }

    SubcomposeAsyncImage(
        model = coil.request.ImageRequest.Builder(androidx.compose.ui.platform.LocalContext.current)
            .data(imageUrl)
            .laximoAuth(BuildConfig.LAXIMO_USER, BuildConfig.LAXIMO_PASS, "ru_RU")
            .build(),
        contentDescription = "Unit image",
        modifier = Modifier
            .fillMaxWidth()
            .height(320.dp)
            .background(Color(0xFFF2F2F2))
    ) {
        // ✅ ВАЖНО: фикс для "implicit receiver" — сохраняем scope явно
        val scope = this

        when (painter.state) {
            is coil.compose.AsyncImagePainter.State.Loading,
            is coil.compose.AsyncImagePainter.State.Empty -> {
                Box(Modifier.fillMaxSize()) {
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                }
            }

            is coil.compose.AsyncImagePainter.State.Error -> {
                Text("Ошибка загрузки изображения", Modifier.padding(12.dp))
            }

            is coil.compose.AsyncImagePainter.State.Success -> {
                val intrinsic = painter.intrinsicSize
                val imgW = intrinsic.width
                val imgH = intrinsic.height

                // если Coil не дал размер — рисуем просто картинку без карты
                if (imgW.isNaN() || imgH.isNaN() || imgW <= 0f || imgH <= 0f) {
                    scope.SubcomposeAsyncImageContent() // ✅ явный receiver
                    return@SubcomposeAsyncImage
                }

                Box(Modifier.fillMaxSize()) {
                    scope.SubcomposeAsyncImageContent() // ✅ явный receiver

                    androidx.compose.foundation.Canvas(
                        modifier = Modifier.matchParentSize()
                    ) {
                        val scaleX = size.width / imgW
                        val scaleY = size.height / imgH

                        mapItems.forEach { item ->
                            val code = item.code
                            val rect = Rect(
                                left = item.x1 * scaleX,
                                top = item.y1 * scaleY,
                                right = item.x2 * scaleX,
                                bottom = item.y2 * scaleY
                            )

                            val isSel = (code != null && code == selectedCode)

                            drawRect(
                                color = if (isSel) Color(0xAA00FF00) else Color(0x550000FF),
                                topLeft = Offset(rect.left, rect.top),
                                size = androidx.compose.ui.geometry.Size(rect.width, rect.height),
                                style = Stroke(width = if (isSel) 4f else 2f)
                            )
                        }
                    }

                    if (mapItems.isNotEmpty()) {
                        Row(
                            Modifier
                                .align(Alignment.BottomCenter)
                                .fillMaxWidth()
                                .background(Color(0x88000000))
                                .padding(6.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            val codes = mapItems.mapNotNull { it.code }.distinct().take(8)
                            codes.forEach { c ->
                                AssistChip(
                                    onClick = { onSelectCode(c) },
                                    label = { Text(c, color = Color.White) }
                                )
                            }
                            AssistChip(
                                onClick = { onSelectCode(null) },
                                label = { Text("Сброс", color = Color.White) }
                            )
                        }
                    }
                }
            }
        }
    }
}