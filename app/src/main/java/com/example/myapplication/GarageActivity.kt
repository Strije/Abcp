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
import com.example.myapplication.abcp.MemoryCache
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.OnResume
import com.example.myapplication.ui.RefreshableContent
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
    var cars by remember { mutableStateOf(MemoryCache.garage) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload) {
        loading = true
        error = null
        try {
            cars = loadGarage(ctx).also { MemoryCache.garage = it }
        } catch (e: Exception) {
            com.example.myapplication.Analytics.error("Гараж → загрузка", e)
            error = "Не удалось загрузить гараж. Проверьте интернет."
        } finally {
            loading = false
        }
    }
    // Вернулись из подбора, где могли добавить машину, — перечитать
    OnResume { reload++ }

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
        RefreshableContent(
            hasData = cars != null, loading = loading, error = error,
            onRefresh = { reload++ }, modifier = Modifier.padding(padding)
        ) {
            val list = cars.orEmpty()
            if (list.isEmpty()) EmptyState(
                "В гараже пока нет машин.\nНайдите свою по VIN, номеру кузова или госномеру и нажмите «＋ В мой гараж» — " +
                    "дальше подбор будет в одно касание."
            ) else LazyColumn(
                modifier = Modifier.fillMaxSize(),
                // Снизу место под кнопку «Найти авто», чтобы она не закрывала последнюю машину
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 12.dp, bottom = 88.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(list) { car ->
                    ElevatedCard(Modifier.fillMaxWidth().clickable { openCar(ctx, car) }) {
                        Column(Modifier.padding(16.dp)) {
                            Text(car.title, style = MaterialTheme.typography.titleMedium)
                            Spacer(Modifier.height(4.dp))
                            Text(car.vin, style = MaterialTheme.typography.bodySmall)
                            Text("Подобрать запчасти →", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
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
