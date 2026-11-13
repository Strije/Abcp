package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.myapplication.SessionManager
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Оформление: оплата, самовывоз/доставка, офис, дата, комментарий → basket/order. */
@OptIn(ExperimentalMaterial3Api::class)
class CheckoutActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val shop = AbcpShop(SessionManager(this))

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var sending by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf<String?>(null) }
                var basket by remember { mutableStateOf<List<BasketItem>>(emptyList()) }
                var opts by remember { mutableStateOf<CheckoutOptions?>(null) }
                // Номера оформленных заказов; пустой список — заказ, скорее всего, создан, но номер не дошёл (сеть).
                // Переживает смерть процесса: иначе после возврата клиент видел бы оформление пустой корзины
                var done by rememberSaveable { mutableStateOf<ArrayList<String>?>(null) }
                // Сколько позиций осталось в корзине после оформления (ABCP взял в заказ не всё)
                var leftover by rememberSaveable { mutableIntStateOf(0) }
                var emptyBasket by remember { mutableStateOf(false) }
                val keyboard = LocalSoftwareKeyboardController.current
                val focus = LocalFocusManager.current

                var payment by remember { mutableStateOf<String?>(null) }
                var method by remember { mutableStateOf<String?>(null) }
                var pickup by remember { mutableStateOf(true) }
                var office by remember { mutableStateOf<String?>(null) }
                var address by remember { mutableStateOf<String?>(null) }
                var date by remember { mutableStateOf<String?>(null) }
                var comment by remember { mutableStateOf("") }
                var reload by remember { mutableIntStateOf(0) }
                var showItems by remember { mutableStateOf(false) }
                // Сообщение, которое переживает перечитывание корзины (например, «изменились цены»)
                var notice by remember { mutableStateOf<String?>(null) }
                fun ecomItems() = basket.map { com.example.myapplication.Analytics.Item(it.brand, it.number, it.description, it.price, it.quantity) }

                LaunchedEffect(reload) {
                    // Заказ уже оформлен (экран восстановлен системой) — корзину и варианты не грузим
                    if (done != null) {
                        loading = false
                        return@LaunchedEffect
                    }
                    loading = true
                    error = null
                    try {
                        basket = shop.basket()
                        emptyBasket = basket.isEmpty()
                        if (emptyBasket) return@LaunchedEffect
                        val o = shop.checkoutOptions(basket)
                        // Перечитали после «изменились цены» — выбор клиента сохраняем, если вариант ещё есть.
                        // Иначе: вариант один — выбираем его сами
                        fun keep(cur: String?, list: List<IdName>) = cur?.takeIf { c -> list.any { it.id == c } } ?: list.singleOrNull()?.id
                        val first = opts == null
                        if (first) com.example.myapplication.Analytics.ecomBeginCheckout(ecomItems())
                        payment = keep(payment, o.payments)
                        method = keep(method, o.shipmentMethods)
                        office = keep(office, o.offices)
                        address = keep(address, o.addresses)
                        date = date?.takeIf { d -> o.dates.any { it.date == d } } ?: o.dates.firstOrNull()?.date
                        if (first) pickup = o.addresses.isEmpty() || o.offices.isNotEmpty()
                        opts = o
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Оформление → варианты доставки", e)
                        error = e.message ?: "Ошибка загрузки"
                    } finally {
                        loading = false
                    }
                }

                val o = opts
                val missing = when {
                    o == null -> "…"
                    o.payments.isNotEmpty() && payment == null -> "Выберите способ оплаты"
                    o.shipmentMethods.isNotEmpty() && method == null -> "Выберите способ доставки"
                    pickup && o.offices.isNotEmpty() && office == null -> "Выберите офис самовывоза"
                    !pickup && address == null -> "Выберите адрес доставки"
                    o.dates.isNotEmpty() && date == null -> "Выберите дату"
                    else -> null
                }

                // Ушёл с оформления без заказа — на каком шаге (что ещё не выбрал) и сколько позиций
                val leaveReason by rememberUpdatedState(missing ?: "не нажал «Подтвердить»")
                DisposableEffect(Unit) {
                    onDispose {
                        if (done == null && opts != null) com.example.myapplication.Analytics.event(
                            "checkout_leave", mapOf("step" to leaveReason, "positions" to basket.size)
                        )
                    }
                }

                Scaffold(topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Оформление заказа") }) }) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            done != null -> Column(
                                Modifier.align(Alignment.Center).padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                val numbers = done.orEmpty()
                                if (numbers.isEmpty()) {
                                    // Связь оборвалась на подтверждении, а корзина после этого пуста
                                    Text(
                                        "Заказ, скорее всего, оформлен — проверьте в «Моих заказах»",
                                        style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center
                                    )
                                } else {
                                    Text("Заказ оформлен", style = MaterialTheme.typography.headlineSmall)
                                    Text("№ " + numbers.joinToString(", "))
                                }
                                if (leftover > 0) {
                                    Spacer(Modifier.height(8.dp))
                                    Text(
                                        "В заказ не попали $leftover поз. — они остались в корзине",
                                        color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center
                                    )
                                }
                                Spacer(Modifier.height(16.dp))
                                com.example.myapplication.push.BatteryHintCard(Modifier.padding(bottom = 16.dp))
                                Button(onClick = {
                                    startActivity(Intent(this@CheckoutActivity, OrdersActivity::class.java))
                                    finish()
                                }) { Text("Мои заказы") }
                            }
                            emptyBasket -> Column(
                                Modifier.align(Alignment.Center).padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                Text("Корзина пуста", style = MaterialTheme.typography.headlineSmall)
                                Spacer(Modifier.height(8.dp))
                                Text("Если вы только что оформляли заказ, он в «Моих заказах».", textAlign = TextAlign.Center)
                                Spacer(Modifier.height(16.dp))
                                Button(onClick = {
                                    startActivity(Intent(this@CheckoutActivity, OrdersActivity::class.java))
                                    finish()
                                }) { Text("Мои заказы") }
                            }
                            o == null -> com.example.myapplication.ui.ErrorState(
                                error ?: "Не удалось загрузить варианты оформления", Modifier.align(Alignment.Center),
                                onRetry = { reload++ }
                            )
                            else -> Column(
                                // imePadding до прокрутки: иначе клавиатура закрывает комментарий и кнопку
                                Modifier.fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(16.dp),
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                val total = basket.sumOf { it.price * it.quantity }
                                Text("${basket.size} поз. на ${formatRub(total)}", style = MaterialTheme.typography.titleMedium)
                                // Что заказываем — свёрнуто; позиции с замечанием ABCP (нет в наличии, цена) видны сразу
                                notice?.let {
                                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                                        Text(it, Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onErrorContainer)
                                    }
                                }
                                val problems = basket.filter { !it.errorMessage.isNullOrBlank() }
                                problems.forEach { b ->
                                    Text("⚠ ${b.brand} ${b.number}: ${b.errorMessage}", color = MaterialTheme.colorScheme.error,
                                        style = MaterialTheme.typography.bodySmall)
                                }
                                TextButton(onClick = { showItems = !showItems }, contentPadding = PaddingValues(0.dp)) {
                                    Text(if (showItems) "Скрыть состав заказа" else "Показать состав заказа")
                                }
                                if (showItems) basket.forEach { b ->
                                    Row(Modifier.fillMaxWidth()) {
                                        Text("${b.brand} ${b.number} × ${b.quantity}", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                        Text(formatRub(b.price * b.quantity), style = MaterialTheme.typography.bodySmall)
                                    }
                                }

                                Choice("Оплата", o.payments, payment) { payment = it }
                                Choice("Способ доставки", o.shipmentMethods, method) { method = it }

                                if (o.addresses.isNotEmpty()) {
                                    Row {
                                        FilterChip(pickup, { pickup = true }, { Text("Самовывоз") })
                                        Spacer(Modifier.width(8.dp))
                                        FilterChip(!pickup, { pickup = false }, { Text("Доставка") })
                                    }
                                }
                                if (pickup) Choice("Офис самовывоза", o.offices, office) { office = it }
                                else Choice("Адрес доставки", o.addresses, address) { address = it }

                                Choice("Дата отгрузки", o.dates.map { IdName(it.date, it.name) }, date) { date = it }

                                OutlinedTextField(
                                    value = comment,
                                    onValueChange = { comment = it },
                                    label = { Text("Комментарий к заказу") },
                                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                                    keyboardActions = KeyboardActions(onDone = {
                                        keyboard?.hide()
                                        focus.clearFocus()
                                    }),
                                    modifier = Modifier.fillMaxWidth()
                                )

                                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

                                Button(
                                    enabled = missing == null && !sending,
                                    modifier = Modifier.fillMaxWidth(),
                                    onClick = {
                                        // Второе нажатие, пока летит первое, создало бы второй заказ
                                        if (sending) return@Button
                                        sending = true
                                        error = null
                                        val choice = OrderChoice(
                                            paymentId = payment,
                                            shipmentMethodId = method,
                                            addressId = if (pickup) "0" else address.orEmpty(),
                                            officeId = if (pickup) office else null,
                                            date = date,
                                            comment = comment
                                        )
                                        val sent = basket
                                        val sentItems = ecomItems()
                                        scope.launch {
                                            try {
                                                val numbers = shop.placeOrder(choice, basketIds = sent.map { it.basketId })
                                                // Статистику отправляем сразу, до любых ожиданий: «Мои заказы» закрывает экран
                                                // и отменяет эту корутину — заказ не должен из-за этого выпасть из отчётов
                                                done = ArrayList(numbers)
                                                com.example.myapplication.RuStoreStore.onOrderPlaced(this@CheckoutActivity)
                                                // Пометка для статистики — в фоне, на оформление не влияет
                                                val app = applicationContext
                                                kotlinx.coroutines.GlobalScope.launch {
                                                    numbers.forEach { n ->
                                                        runCatching { com.example.myapplication.server.AppServer(app).markOrderFromApp(n) }
                                                    }
                                                }
                                                com.example.myapplication.Analytics.event("order_placed", mapOf("positions" to sent.size))
                                                com.example.myapplication.Analytics.purchase(numbers.joinToString(","), sentItems)
                                                // Корзину перечитываем последней и там, где закрытие экрана её не отменит.
                                                // Что-то осталось — ABCP взял в заказ не всё, говорим об этом
                                                kotlinx.coroutines.GlobalScope.launch(Dispatchers.Main) {
                                                    runCatching { shop.basket() }.onSuccess {
                                                        MemoryCache.basket = it
                                                        CartState.count = it.size
                                                        leftover = it.size
                                                        if (it.isNotEmpty()) com.example.myapplication.Analytics.event(
                                                            "order_partial", mapOf("left" to it.size)
                                                        )
                                                    }
                                                }
                                            } catch (e: CancellationException) {
                                                throw e
                                            } catch (e: AbcpNetworkError) {
                                                com.example.myapplication.Analytics.error("Оформление → подтвердить заказ", e)
                                                // Ответ не дошёл, а заказ мог создаться: повтор вслепую — второй заказ. Сверяемся по корзине
                                                val now = try {
                                                    shop.basket()
                                                } catch (c: CancellationException) {
                                                    throw c
                                                } catch (c: Exception) {
                                                    null
                                                }
                                                when {
                                                    now == null -> {
                                                        error = (e.message ?: "Связь прервалась.") +
                                                            "\nЗаказ мог оформиться — прежде чем повторять, проверьте «Мои заказы»."
                                                    }
                                                    now.isEmpty() -> {
                                                        MemoryCache.basket = now
                                                        CartState.count = 0
                                                        com.example.myapplication.Analytics.event("order_placed_unconfirmed", mapOf("positions" to sent.size))
                                                        done = ArrayList()
                                                    }
                                                    now.size < sent.size -> {
                                                        basket = now
                                                        CartState.count = now.size
                                                        error = "Связь прервалась. Часть позиций, похоже, уже ушла в заказ — проверьте «Мои заказы». " +
                                                            "В корзине осталось ${now.size} поз."
                                                    }
                                                    else -> {
                                                        error = e.message ?: "Заказ не оформлен"
                                                    }
                                                }
                                            } catch (e: Exception) {
                                                com.example.myapplication.Analytics.error("Оформление → подтвердить заказ", e)
                                                if (isBasketChanged(e.message)) {
                                                    // Цены/наличие обновились: перечитываем корзину — изменившиеся позиции получат ⚠
                                                    notice = "Цена или наличие некоторых позиций изменились с момента, как вы их положили в корзину. " +
                                                        "Проверьте позиции с ⚠ ниже (или удалите их в корзине) и подтвердите заказ ещё раз."
                                                    showItems = true
                                                    reload++
                                                } else {
                                                    error = e.message ?: "Заказ не оформлен"
                                                }
                                            } finally {
                                                sending = false
                                            }
                                        }
                                    }
                                ) { Text(if (sending) "Отправляем…" else missing ?: "Подтвердить заказ") }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Выбор одного варианта из списка ABCP; пустой список — блок не показываем. */
@Composable
private fun Choice(title: String, options: List<IdName>, selected: String?, onSelect: (String) -> Unit) {
    if (options.isEmpty()) return
    Column {
        Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        options.forEach { opt ->
            Row(
                Modifier.fillMaxWidth()
                    .selectable(selected = opt.id == selected, onClick = { onSelect(opt.id) })
                    .padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                RadioButton(selected = opt.id == selected, onClick = { onSelect(opt.id) })
                Text(opt.name)
            }
        }
    }
}
