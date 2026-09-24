package com.example.myapplication

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

        val prefillVin = intent.getStringExtra("prefillVin").orEmpty()

        setContent {
            MaterialTheme {
                val ctx = LocalContext.current
                val scope = rememberCoroutineScope()

                var vinText by remember { mutableStateOf(prefillVin) }
                var loading by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf<String?>(null) }
                var results by remember { mutableStateOf<List<LaximoVehicleContext>>(emptyList()) }

                fun onSearchClick() {
                    val vin = vinText.trim()
                    if (vin.isBlank()) {
                        error = "Введите VIN, номер кузова или госномер"
                        return
                    }

                    loading = true
                    error = null
                    results = emptyList()

                    scope.launch {
                        try {
                            val list = withContext(Dispatchers.IO) {
                                repo.findVehicleAny(vin)
                            }
                            results = list
                        } catch (e: Exception) {
                            Log.e("VIN_SEARCH", "EX", e)
                            error = e.message ?: e.javaClass.simpleName
                        } finally {
                            loading = false
                        }
                    }
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
                            modifier = Modifier.fillMaxWidth()
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
                                text = "Ошибка: $error",
                                color = MaterialTheme.colorScheme.error
                            )
                        }

                        if (loading) {
                            Box(Modifier.fillMaxWidth()) {
                                CircularProgressIndicator(Modifier.align(Alignment.Center))
                            }
                        }

                        if (!loading && error == null) {
                            if (results.isEmpty()) {
                                Text("Ничего не найдено")
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
                                                Text("Каталог: ${r.catalog}", style = MaterialTheme.typography.bodySmall)
                                                if (r.attributes.isNotEmpty()) {
                                                    Spacer(Modifier.height(6.dp))
                                                    r.attributes.forEach { attr ->
                                                        if (!attr.value.isNullOrBlank()) {
                                                            Text(
                                                                "${attr.name ?: attr.key}: ${attr.value}",
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
    }
}
