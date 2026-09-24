package com.example.myapplication

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.AbcpGarageRepository
import com.example.myapplication.abcp.GarageCar
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class GarageActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { GarageScreen() } }
    }
}

/** Гараж из ABCP; нажатие на машину — подбор запчастей по VIN/кузову/госномеру. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GarageScreen() {
    val ctx = LocalContext.current
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var cars by remember { mutableStateOf<List<GarageCar>>(emptyList()) }

    LaunchedEffect(Unit) {
        try {
            cars = loadGarage(ctx)
        } catch (e: Exception) {
            error = e.message ?: e.javaClass.simpleName
        } finally {
            loading = false
        }
    }

    Scaffold(
        topBar = { TopAppBar(title = { Text("Мой гараж") }) },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { ctx.startActivity(Intent(ctx, VinSearchActivity::class.java)) },
                icon = { Icon(Icons.Default.Add, null) },
                text = { Text("Найти авто") }
            )
        }
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                error != null -> Text("Ошибка: $error", Modifier.padding(16.dp))
                cars.isEmpty() -> Text(
                    "В гараже пока нет машин. Найдите свою по VIN или госномеру.",
                    Modifier.padding(16.dp)
                )
                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    items(cars) { car ->
                        ElevatedCard(Modifier.fillMaxWidth().clickable { openCar(ctx, car) }) {
                            Column(Modifier.padding(16.dp)) {
                                Text(car.title, style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(4.dp))
                                Text(car.vin, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
        }
    }
}

suspend fun loadGarage(ctx: Context): List<GarageCar> {
    val session = SessionManager(ctx)
    return withContext(Dispatchers.IO) {
        AbcpGarageRepository().loadGarage(session.login(), session.passMd5())
    }
}

fun openCar(ctx: Context, car: GarageCar) {
    ctx.startActivity(Intent(ctx, VinSearchActivity::class.java).putExtra("prefillVin", car.vin))
}
