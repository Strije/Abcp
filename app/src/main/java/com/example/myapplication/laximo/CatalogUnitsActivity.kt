package com.example.myapplication.laximo
import com.example.myapplication.ui.theme.AvtodrugTheme

import com.example.myapplication.laximo.resolveLaximoImageUrl
import android.content.Intent
import android.os.Build
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
import coil.ImageLoader
import coil.compose.SubcomposeAsyncImage
import coil.decode.GifDecoder
import coil.decode.ImageDecoderDecoder
import coil.request.ImageRequest
import com.example.myapplication.BuildConfig
import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class CatalogUnitsActivity : ComponentActivity() {

    private val repo: LaximoRepository by lazy {
        LaximoRepository(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val ssd = intent.getStringExtra("ssd").orEmpty() // SSD категории!
        val categoryId = intent.getStringExtra("categoryId").orEmpty()
        val categoryName = intent.getStringExtra("categoryName").orEmpty()

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)
        val category = LaximoCategory(
            categoryId = categoryId,
            name = categoryName,
            ssd = ssd,
            childrens = false
        )

        setContent {
            AvtodrugTheme {
                val context = LocalContext.current

                // ✅ ImageLoader с поддержкой GIF
                val imageLoader = remember {
                    ImageLoader.Builder(context)
                        .components {
                            if (Build.VERSION.SDK_INT >= 28) {
                                add(ImageDecoderDecoder.Factory())
                            } else {
                                add(GifDecoder.Factory())
                            }
                        }
                        .build()
                }

                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var units by remember { mutableStateOf<List<LaximoUnit>>(emptyList()) }

                LaunchedEffect(categoryId, ssd) {
                    loading = true
                    error = null
                    scope.launch {
                        try {
                            units = repo.listUnits(ctx, category)
                        } catch (e: Exception) {
                            com.example.myapplication.Analytics.error("Каталог → узлы", e)
                            error = laximoUserMessage(e)
                        } finally {
                            loading = false
                        }
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text(categoryName.ifBlank { "Узлы" }) }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(16.dp))
                            else -> {
                                LazyColumn(
                                    modifier = Modifier.fillMaxSize(),
                                    contentPadding = PaddingValues(12.dp),
                                    verticalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    items(units) { u ->
                                        val url = remember(u.imageUrl) { resolveLaximoImageUrl(u.imageUrl, LaximoImageSize.SOURCE) }

                                        ElevatedCard(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .clickable {
                                                    val i = Intent(
                                                        this@CatalogUnitsActivity,
                                                        UnitDetailsActivity::class.java
                                                    )
                                                    i.putExtra("catalog", catalog)
                                                    i.putExtra("vehicleId", vehicleId)

                                                    // ⚠️ ВАЖНО: SSD УЗЛА
                                                    i.putExtra("unitSsd", u.ssd)

                                                    i.putExtra("unitId", u.unitId)
                                                    i.putExtra("unitName", u.name)

                                                    // Лучше передавать уже "починенный" url
                                                    i.putExtra("imageUrl", url)

                                                    startActivity(i)
                                                }
                                        ) {
                                            Row(
                                                modifier = Modifier
                                                    .fillMaxWidth()
                                                    .padding(12.dp),
                                                verticalAlignment = Alignment.CenterVertically
                                            ) {
                                                // ✅ SubcomposeAsyncImage покажет лоадер/ошибку
                                                SubcomposeAsyncImage(
                                                    model = ImageRequest.Builder(context)
                                                        .data(url)
                                                        .crossfade(true)
                                                        .listener(
                                                            onError = { _, result ->
                                                                Log.e(
                                                                    "LAXIMO_IMG",
                                                                    "Image load error url=$url, unitId=${u.unitId}",
                                                                    result.throwable
                                                                )
                                                            }
                                                        )
                                                        .build(),
                                                    imageLoader = imageLoader,
                                                    contentDescription = u.name,
                                                    modifier = Modifier.size(72.dp),
                                                    loading = {
                                                        Box(
                                                            Modifier.size(72.dp),
                                                            contentAlignment = Alignment.Center
                                                        ) {
                                                            CircularProgressIndicator(
                                                                modifier = Modifier.size(20.dp),
                                                                strokeWidth = 2.dp
                                                            )
                                                        }
                                                    },
                                                    error = {
                                                        Box(
                                                            Modifier.size(72.dp),
                                                            contentAlignment = Alignment.Center
                                                        ) {
                                                            Text("X", style = MaterialTheme.typography.titleMedium)
                                                        }
                                                    }
                                                )

                                                Spacer(Modifier.width(12.dp))

                                                Column(Modifier.weight(1f)) {
                                                    Text(
                                                        text = u.name,
                                                        style = MaterialTheme.typography.titleSmall
                                                    )
                                                    Spacer(Modifier.height(4.dp))
                                                    Text(
                                                        text = "Код: ${u.code ?: "—"}",
                                                        style = MaterialTheme.typography.bodySmall
                                                    )
                                                    Text(
                                                        text = "unitId: ${u.unitId}",
                                                        style = MaterialTheme.typography.bodySmall
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

/**
 * Laximo отдаёт ссылки с плейсхолдером %size% — его нужно заменить.
 * Пример: .../%size%/... -> .../240/...
 */