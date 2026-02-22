package com.example.myapplication

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.example.myapplication.laximo.LaximoApiException
import com.example.myapplication.laximo.LaximoClient
import com.example.myapplication.laximo.LaximoRepository
import com.example.myapplication.laximo.resolveLaximoImage
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
class UnitInfoActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog").orEmpty()
        val vehicleId = intent.getStringExtra("vehicleId").orEmpty()
        val unitId = intent.getStringExtra("unitId").orEmpty()
        val unitName = intent.getStringExtra("unitName").orEmpty()
        val unitSsd = intent.getStringExtra("unitSsd").orEmpty() // <-- как в UnitDetailsActivity
        val imageUrl = intent.getStringExtra("imageUrl")

        val repo = LaximoRepository(
            LaximoClient(
                username = BuildConfig.LAXIMO_USER,
                password = BuildConfig.LAXIMO_PASS,
                language = "ru_RU"
            )
        )

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = unitSsd)
        val unit = LaximoUnit(unitId = unitId, name = unitName, ssd = unitSsd, imageUrl = imageUrl)

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var text by remember { mutableStateOf<String>("") }

                LaunchedEffect(unitId, unitSsd) {
                    loading = true
                    error = null
                    try {
                        val details = withContext(Dispatchers.IO) {
                            repo.listDetailByUnit(ctx, unit)
                        }
                        text = buildString {
                            appendLine("details=${details.size}")
                            details.take(50).forEach { d ->
                                appendLine("- ${d.codeOnImage ?: "—"} | ${d.oem ?: "—"} | ${d.name ?: "—"}")
                            }
                        }
                    } catch (e: LaximoApiException) {
                        error = e.pretty
                    } catch (e: Exception) {
                        Log.e("LAXIMO_UNITINFO", "EX ${e.message}", e)
                        error = e.message ?: "Ошибка загрузки"
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text(unitName.ifBlank { "Узел $unitId" }) }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(16.dp))
                            else -> Column(
                                Modifier
                                    .fillMaxSize()
                                    .padding(16.dp),
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                val big = imageUrl.resolveLaximoImage(800)
                                if (!big.isNullOrBlank()) {
                                    AsyncImage(
                                        model = big,
                                        contentDescription = unitName,
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .height(260.dp)
                                    )
                                }

                                ElevatedCard(Modifier.fillMaxWidth()) {
                                    Text(
                                        text = text.ifBlank { "—" },
                                        modifier = Modifier.padding(12.dp),
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