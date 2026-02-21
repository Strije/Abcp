package com.example.myapplication

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.example.myapplication.laximo.LaximoApiException
import com.example.myapplication.laximo.LaximoClient
import com.example.myapplication.laximo.LaximoRepository
import com.example.myapplication.laximo.model.LaximoVehicleContext
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
class VinSearchActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val laximoClient = LaximoClient(
            username = BuildConfig.LAXIMO_USER,
            password = BuildConfig.LAXIMO_PASS,
            language = "ru_RU"
        )
        val repo = LaximoRepository(laximoClient)

        setContent {
            MaterialTheme {
                val scope = rememberCoroutineScope()

                var query by remember { mutableStateOf("") }
                var loading by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf<String?>(null) }
                var rawResult by remember { mutableStateOf<String?>(null) }

                Scaffold(
                    topBar = { TopAppBar(title = { Text("Каталог по VIN/номеру") }) }
                ) { padding ->
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(padding)
                            .padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text("Введите VIN или гос. номер", style = MaterialTheme.typography.titleMedium)

                        OutlinedTextField(
                            value = query,
                            onValueChange = { query = it },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                            label = { Text("VIN или гос. номер") },
                            keyboardOptions = KeyboardOptions(
                                keyboardType = KeyboardType.Ascii,
                                imeAction = ImeAction.Done
                            )
                        )

                        if (error != null) {
                            Text(error!!, color = MaterialTheme.colorScheme.error)
                        }

                        Button(
                            enabled = !loading,
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                val q = query.trim()
                                if (q.isBlank()) {
                                    error = "Введите VIN или гос. номер."
                                    return@Button
                                }

                                loading = true
                                error = null
                                rawResult = null

                                scope.launch {
                                    try {
                                        val json = withContext(Dispatchers.IO) {
                                            val isVin = q.length == 17
                                            if (isVin) repo.findVehicle(q) else repo.findVehicleByPlateNumber(q)
                                        }

                                        val ctx = parseVehicleContext(json)

                                        startActivity(
                                            Intent(this@VinSearchActivity, CatalogCategoriesActivity::class.java).apply {
                                                putExtra("catalog", ctx.catalog)
                                                putExtra("vehicleId", ctx.vehicleId)
                                                putExtra("ssd", ctx.ssd)
                                                putExtra("brand", ctx.brand)
                                                putExtra("name", ctx.name)
                                            }
                                        )

                                        Log.d("LAXIMO", "result=$json")
                                        rawResult = json
                                    } catch (e: LaximoApiException) {
                                        error = e.pretty
                                    } catch (e: Exception) {
                                        Log.e("LAXIMO", "EX class=${e::class.java.name} msg=${e.message}", e)
                                        error = "Laximo: ${e::class.java.simpleName}: ${e.message ?: "no message"}"
                                    } finally {
                                        loading = false
                                    }
                                }
                            }
                        ) {
                            Text(if (loading) "Поиск..." else "Найти")
                        }

                        if (rawResult != null) {
                            ElevatedCard(Modifier.fillMaxWidth()) {
                                Text(
                                    text = rawResult!!,
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

fun parseVehicleContext(raw: String): LaximoVehicleContext {
    val arr = JsonParser.parseString(raw).asJsonArray
    val o = arr[0].asJsonObject
    return LaximoVehicleContext(
        catalog = o["catalog"].asString,
        brand = o["brand"].asString,
        name = o["name"].asString,
        vehicleId = o["vehicleId"].asString,
        ssd = o["ssd"].asString
    )
}