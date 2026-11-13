package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.SearchActivity
import com.example.myapplication.SessionManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.RefreshableContent

class CartActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { CartScreen(0) } }
    }
}

/** reloadKey меняется — корзина перечитывается (возврат на экран, смена вкладки). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CartScreen(reloadKey: Int) {
    val ctx = LocalContext.current
    val shop = remember { AbcpShop(SessionManager(ctx)) }
    var localReload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    // Ошибка — обрыв связи: такую пробуем перечитать сами, когда сеть вернётся
    var networkError by remember { mutableStateOf(false) }
    var autoRetries by remember { mutableIntStateOf(0) }
    var basket by remember { mutableStateOf(MemoryCache.basket) }
    // Позиция, которую сейчас меняем (у неё крутится индикатор). Пока идёт любой запрос, заблокированы все
    // «−/+», «Удалить» и «Оформить»: второе изменение поверх первого считалось бы от старого количества
    var busy by remember { mutableStateOf<String?>(null) }
    // Идёт очистка и возврат позиций — экран закрыт диалогом
    var rebuilding by remember { mutableStateOf(false) }
    // Что не вернулось в корзину после пересборки
    var notReturned by remember { mutableStateOf<List<String>?>(null) }
    // Сбой, после которого клиенту надо что-то сделать самому, — диалогом: тост легко пропустить
    var problem by remember { mutableStateOf<String?>(null) }
    var confirmDelete by remember { mutableStateOf<BasketItem?>(null) }
    // Позиции, которые ABCP поштучно не удаляет (устарели) — убрать их можно только очисткой корзины
    var confirmRebuild by remember { mutableStateOf<Set<BasketItem>?>(null) }

    LaunchedEffect(reloadKey, localReload) {
        loading = true
        error = null
        try {
            basket = shop.basket().also { MemoryCache.basket = it; CartState.count = it.size }
            networkError = false
            autoRetries = 0
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            com.example.myapplication.Analytics.error("Корзина → загрузка", e)
            networkError = e is AbcpNetworkError
            error = com.example.myapplication.ui.userMessage(e, "Не удалось загрузить корзину. Попробуйте ещё раз.")
        } finally {
            loading = false
        }
    }

    // Связь пропала — пока висит ошибка, раз в 5 секунд пробуем перечитать сами (не больше 6 раз подряд)
    LaunchedEffect(error, loading) {
        if (error != null && networkError && !loading && autoRetries < 6) {
            kotlinx.coroutines.delay(5000)
            autoRetries++
            localReload++
        }
    }

    // Вернулись с оформления или из поиска — перечитать
    com.example.myapplication.ui.OnResume { localReload++ }

    fun mutate(b: BasketItem, action: suspend () -> Unit) {
        if (busy != null) return
        busy = b.itemKey
        scope.launch {
            try {
                action()
            } catch (e: CancellationException) {
                throw e
            } catch (e: StaleBasketItem) {
                com.example.myapplication.Analytics.error("Корзина → устаревшая позиция", e)
                confirmRebuild = setOf(b)
            } catch (e: BasketPositionLost) {
                com.example.myapplication.Analytics.error("Корзина → позиция пропала", e)
                problem = e.message
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Корзина → изменение", e)
                Toast.makeText(ctx, com.example.myapplication.ui.userMessage(e, "Не удалось изменить корзину. Попробуйте ещё раз."), Toast.LENGTH_LONG).show()
            } finally {
                busy = null
                // И после ошибки тоже: запрос мог дойти (таймаут), и «+» по старому количеству задвоил бы позицию
                localReload++
            }
        }
    }

    val items = basket.orEmpty()
    val locked = busy != null || loading
    val total = items.sumOf { it.price * it.quantity }

    Scaffold(
        topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Корзина") }, actions = { com.example.myapplication.ui.SearchAction() }) },
        bottomBar = {
            if (items.isNotEmpty()) {
                Surface(tonalElevation = 3.dp) {
                    // Android 15+ рисует экран до самого низа — отступ от панели навигации
                    Row(
                        Modifier.fillMaxWidth().navigationBarsPadding().padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("Итого · ${items.size} поз.", style = MaterialTheme.typography.bodySmall)
                            Text(formatRub(total), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        }
                        Button(
                            enabled = !locked,
                            onClick = {
                                com.example.myapplication.Analytics.event("checkout_open", mapOf("positions" to items.size))
                                ctx.startActivity(Intent(ctx, CheckoutActivity::class.java))
                            }
                        ) { Text("Оформить") }
                    }
                }
            }
        }
    ) { padding ->
        RefreshableContent(
            hasData = basket != null, loading = loading, error = error,
            onRefresh = { localReload++ }, modifier = Modifier.padding(padding)
        ) {
            if (items.isEmpty()) EmptyState("Корзина пуста", "Найти запчасть") {
                ctx.startActivity(Intent(ctx, SearchActivity::class.java))
            } else LazyColumn(
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Список на экране, а обновить не вышло — говорим об этом и даём повторить
                error?.let { e ->
                    item {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.weight(1f))
                            TextButton(onClick = { autoRetries = 0; localReload++ }, enabled = !loading) { Text("Повторить") }
                        }
                    }
                }
                val stale = items.filter { !it.errorMessage.isNullOrBlank() }
                if (stale.isNotEmpty()) item {
                    OutlinedButton(
                        onClick = { confirmRebuild = stale.toSet() }, enabled = !locked,
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Удалить позиции с ⚠ (${stale.size})", color = MaterialTheme.colorScheme.error) }
                }
                // itemKey у ABCP не уникален (так и сказано в документации) — две одинаковые строки корзины
                // с одним ключом роняли экран (AppMetrica: 25 вылетов «Key … was already used»). С номером строки повтор невозможен.
                items(items.withIndex().toList(), key = { (i, b) -> "$i|${b.basketId}|${b.positionId}|${b.itemKey}" }) { (_, b) ->
                    val isBusy = busy == b.itemKey
                    ElevatedCard(Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                // Нажатие на номер — цены и аналоги этой детали
                                Text(
                                    "${b.brand} ${b.number}", style = MaterialTheme.typography.titleSmall,
                                    color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.clickable { com.example.myapplication.ui.openOffers(ctx, b.brand, b.number, b.description) }
                                )
                                if (b.description.isNotBlank()) Text(b.description, style = MaterialTheme.typography.bodySmall)
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "${formatRub(b.price)} × ${b.quantity} = ${formatRub(b.price * b.quantity)}",
                                    fontWeight = FontWeight.Bold
                                )
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    OutlinedButton(
                                        onClick = { mutate(b) { shop.changeQuantity(b, b.quantity - b.packing) } },
                                        enabled = !locked && b.quantity > b.packing,
                                        contentPadding = PaddingValues(0.dp),
                                        modifier = Modifier.size(48.dp)
                                    ) { Text("−") }
                                    Box(Modifier.widthIn(min = 64.dp), contentAlignment = Alignment.Center) {
                                        if (isBusy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                                        else Text("${b.quantity} шт.")
                                    }
                                    OutlinedButton(
                                        onClick = { mutate(b) { shop.changeQuantity(b, b.quantity + b.packing) } },
                                        enabled = !locked,
                                        contentPadding = PaddingValues(0.dp),
                                        modifier = Modifier.size(48.dp)
                                    ) { Text("+") }
                                }
                                Text("Срок: ${formatDelivery(b.deadlineHours, b.deadlineHoursMax)}", style = MaterialTheme.typography.bodySmall)
                                b.errorMessage?.let { Text("⚠ $it", color = MaterialTheme.colorScheme.error) }
                            }
                            IconButton(enabled = !locked, onClick = { confirmDelete = b }) { Icon(Icons.Default.Delete, "Удалить") }
                        }
                    }
                }
            }
        }
    }

    confirmDelete?.let { b ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Удалить из корзины?") },
            text = { Text("${b.brand} ${b.number}" + if (b.description.isNotBlank()) "\n${b.description}" else "") },
            confirmButton = {
                TextButton(onClick = { confirmDelete = null; mutate(b) { shop.removeFromBasket(b) } }) { Text("Удалить") }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Отмена") } }
        )
    }

    confirmRebuild?.let { drop ->
        val keep = items.size - drop.size
        AlertDialog(
            onDismissRequest = { confirmRebuild = null },
            title = { Text(if (drop.size == 1) "Позиция устарела" else "Удалить устаревшие позиции?") },
            text = {
                Text(
                    drop.joinToString("\n") { "${it.brand} ${it.number}" } + "\n\n" +
                        "Поставщик обновил прайс, и такие позиции не удаляются по одной. Очистим корзину" +
                        (if (keep > 0) " и сразу положим обратно остальные позиции ($keep)." else ".")
                )
            },
            confirmButton = {
                TextButton(enabled = busy == null, onClick = {
                    confirmRebuild = null
                    busy = "rebuild"
                    rebuilding = true
                    val all = items
                    val app = ctx.applicationContext
                    scope.launch {
                        // Клиент мог уйти с экрана — всё равно доводим до конца и говорим, что не вернулось
                        withContext(NonCancellable) {
                            try {
                                val failed = shop.rebuildBasket(all, drop)
                                com.example.myapplication.Analytics.event("basket_rebuild", mapOf("dropped" to drop.size, "failed" to failed.size))
                                if (failed.isNotEmpty()) {
                                    notReturned = failed
                                    // Экрана уже нет — диалог показать негде
                                    if (!scope.isActive) Toast.makeText(
                                        app, "Не удалось вернуть в корзину: ${failed.joinToString()}. Добавьте их заново через поиск.",
                                        Toast.LENGTH_LONG
                                    ).show()
                                }
                            } catch (e: Exception) {
                                com.example.myapplication.Analytics.error("Корзина → очистка", e)
                                problem = com.example.myapplication.ui.userMessage(e, "Не удалось очистить корзину. Проверьте её и попробуйте ещё раз.")
                            } finally {
                                busy = null
                                rebuilding = false
                                localReload++
                            }
                        }
                    }
                }) { Text("Удалить") }
            },
            dismissButton = { TextButton(onClick = { confirmRebuild = null }) { Text("Отмена") } }
        )
    }

    // Пересборку прерывать нельзя — закрыть этот диалог нечем, он уходит сам
    if (rebuilding) AlertDialog(
        onDismissRequest = {},
        properties = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false),
        confirmButton = {},
        title = { Text("Обновляем корзину…") },
        text = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(16.dp))
                Text("Не закрывайте экран, пока мы возвращаем позиции в корзину.")
            }
        }
    )

    notReturned?.let { lost ->
        AlertDialog(
            onDismissRequest = { notReturned = null },
            title = { Text("Не все позиции вернулись") },
            text = { Text("Не удалось вернуть в корзину:\n" + lost.joinToString("\n") + "\n\nДобавьте их заново через поиск.") },
            confirmButton = { TextButton(onClick = { notReturned = null }) { Text("Понятно") } }
        )
    }

    problem?.let { text ->
        // Закрыли диалог — перечитываем корзину: на экране должно быть то, что в ней на самом деле
        AlertDialog(
            onDismissRequest = { problem = null; localReload++ },
            title = { Text("Корзина") },
            text = { Text(text) },
            confirmButton = { TextButton(onClick = { problem = null; localReload++ }) { Text("Понятно") } }
        )
    }
}
