package com.example.myapplication

import android.content.Intent
import android.os.Bundle
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
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.AbcpGarageRepository
import com.example.myapplication.abcp.GarageCar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
class GarageActivity : ComponentActivity() {

    private val repo = AbcpGarageRepository()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ВРЕМЕННО (быстро): прямо тут, чтобы у тебя всё заработало.
        // Потом перенесём в BuildConfig как LAXIMO_USER/PASS.
        val userLogin = "m.r.strizh@gmail.com"
        val userPsw = "88a33660393da77e2f44b9373c4b0138"

        setContent {
            MaterialTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var cars by remember { mutableStateOf<List<GarageCar>>(emptyList()) }

                LaunchedEffect(Unit) {
                    scope.launch {
                        loading = true
                        error = null
                        try {
                            cars = withContext(Dispatchers.IO) {
                                repo.loadGarage(userLogin, userPsw)
                            }
                        } catch (e: Exception) {
                            error = e.message ?: e.javaClass.simpleName
                        } finally {
                            loading = false
                        }
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text("Мой гараж") }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text("Ошибка: $error", Modifier.padding(16.dp))
                            cars.isEmpty() -> Text("В гараже нет авто", Modifier.padding(16.dp))
                            else -> LazyColumn(
                                modifier = Modifier.fillMaxSize(),
                                contentPadding = PaddingValues(12.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                items(cars) { car ->
                                    ElevatedCard(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clickable {
                                                val i = Intent(this@GarageActivity, VinSearchActivity::class.java)
                                                i.putExtra("prefillVin", car.vin)
                                                startActivity(i)
                                            }
                                    ) {
                                        Column(Modifier.padding(12.dp)) {
                                            Text(car.title, style = MaterialTheme.typography.titleMedium)
                                            Spacer(Modifier.height(4.dp))
                                            Text("VIN: ${car.vin}", style = MaterialTheme.typography.bodySmall)
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