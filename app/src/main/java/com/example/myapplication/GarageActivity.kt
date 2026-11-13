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
import kotlinx.coroutines.launch
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.GarageCache
import com.example.myapplication.abcp.GarageCar
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.OnResume
import com.example.myapplication.ui.RefreshableContent
import com.example.myapplication.ui.theme.AvtodrugTheme

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
    // Прошлый список (из памяти или с диска) показываем сразу
    var cars by remember { mutableStateOf(GarageCache.peek(ctx)) }
    var reload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    var favorite by remember { mutableStateOf(com.example.myapplication.abcp.GarageFavorite.get(ctx)) }
    var toDelete by remember { mutableStateOf<GarageCar?>(null) }

    LaunchedEffect(reload) {
        loading = true
        error = null
        try {
            cars = loadGarage(ctx)
        } catch (e: Exception) {
            com.example.myapplication.Analytics.error("Гараж → загрузка", e)
            error = "Не удалось загрузить гараж. Проверьте интернет."
        } finally {
            loading = false
        }
    }
    // Вернулись из подбора, где могли добавить машину: добавление сбрасывает кэш, иначе в сеть не идём
    OnResume { reload++ }

    toDelete?.let { car ->
        DeleteCarDialog(car, onDismiss = { toDelete = null }) {
            toDelete = null
            scope.launch {
                try {
                    com.example.myapplication.abcp.AbcpShop(SessionManager(ctx)).deleteFromGarage(car.id)
                    if (favorite == car.vin) { favorite = null; com.example.myapplication.abcp.GarageFavorite.set(ctx, null) }
                    GarageCache.invalidate(ctx)
                    reload++
                } catch (e: Exception) {
                    com.example.myapplication.Analytics.error("Гараж → удалить", e)
                    android.widget.Toast.makeText(ctx, e.message ?: "Не удалось удалить", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    Scaffold(
        topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Мой гараж") }) },
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
            // Потянули вниз — человек хочет свежий список, кэш не в счёт
            onRefresh = { GarageCache.invalidate(ctx); reload++ }, modifier = Modifier.padding(padding)
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
                // Обновить не вышло, а список уже показан — говорим об этом, а не молчим
                error?.let { msg ->
                    item {
                        Text(
                            "$msg Показан прежний список — потяните вниз, чтобы обновить.",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
                item { com.example.myapplication.OneTimeHint("garage_star", "Нажмите ☆ у машины — она станет «Моей машиной» на главной, и запчасти для неё будут в одно касание.") }
                items(list.sortedByDescending { it.vin == favorite }) { car ->
                    val isFav = car.vin == favorite
                    ElevatedCard(Modifier.fillMaxWidth().clickable { openCar(ctx, car) }) {
                        Row(Modifier.padding(start = 16.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f).padding(vertical = 8.dp)) {
                                Text(car.title, style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(4.dp))
                                Text(car.vin, style = MaterialTheme.typography.bodySmall)
                                if (isFav) Text("На главной — «Моя машина»", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text("Подобрать запчасти →", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                            }
                            // ★ — избранная: её показывает главная
                            IconButton(onClick = {
                                favorite = if (isFav) null else car.vin
                                com.example.myapplication.abcp.GarageFavorite.set(ctx, favorite)
                            }) {
                                Text(if (isFav) "★" else "☆", style = MaterialTheme.typography.headlineSmall,
                                    color = if (isFav) androidx.compose.ui.graphics.Color(0xFFF2B01E) else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            if (car.id.isNotBlank()) IconButton(onClick = { toDelete = car }) {
                                Icon(Icons.Default.Delete, "Удалить из гаража", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DeleteCarDialog(car: GarageCar, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Удалить из гаража?") },
        text = { Text("${car.title}\n${car.vin}") },
        confirmButton = { TextButton(onClick = onConfirm) { Text("Удалить") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } }
    )
}

/** Гараж через общий кэш: в ABCP — не чаще раза в 10 минут и после изменения гаража. */
suspend fun loadGarage(ctx: Context): List<GarageCar> = GarageCache.load(ctx)

fun openCar(ctx: Context, car: GarageCar) {
    ctx.startActivity(Intent(ctx, VinSearchActivity::class.java).putExtra("prefillVin", car.vin))
}
