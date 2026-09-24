package com.example.myapplication

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.CartScreen
import com.example.myapplication.abcp.CartState
import com.example.myapplication.abcp.GarageCar
import com.example.myapplication.abcp.NotificationsActivity
import com.example.myapplication.abcp.OrderStatusWatch
import com.example.myapplication.abcp.OrdersScreen
import com.example.myapplication.abcp.AbcpShop
import com.example.myapplication.abcp.formatRub
import com.example.myapplication.laximo.normalizeRuPlate
import com.example.myapplication.server.AppServer
import com.example.myapplication.server.Finance
import kotlinx.coroutines.launch
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.google.gson.Gson

private enum class HomeTab(val title: String, val icon: ImageVector) {
    Home("Главная", Icons.Default.Home),
    Search("Поиск", Icons.Default.Search),
    Garage("Гараж", Icons.Default.Build),
    Orders("Заказы", Icons.Default.List),
    Cart("Корзина", Icons.Default.ShoppingCart)
}

/** Основной экран после входа: нижнее меню (зона большого пальца) и вкладки. */
class MainShellActivity : ComponentActivity() {

    /** Непрочитанные в ленте уведомлений — перечитываем при каждом возврате на экран */
    private var unread by mutableIntStateOf(0)

    override fun onResume() {
        super.onResume()
        unread = OrderStatusWatch.unreadCount(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val session = SessionManager(this)
        if (!session.isLoggedIn()) {
            startActivity(Intent(this, MainActivity::class.java)); finish(); return
        }
        // Фоновая проверка статусов заказов + разрешение на уведомления (Android 13+)
        OrderStatusWatch.schedule(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
                .launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        val user = intent.getStringExtra(EXTRA_USER_JSON)?.let {
            runCatching { Gson().fromJson(it, UserInfoDto::class.java) }.getOrNull()
        }

        setContent {
            AvtodrugTheme {
                var tab by remember { mutableStateOf(HomeTab.Home) }
                var visits by remember { mutableIntStateOf(0) } // для перечитывания корзины при входе на вкладку
                LaunchedEffect(Unit) { CartState.refresh(AbcpShop(session)) }

                Scaffold(
                    bottomBar = {
                        NavigationBar {
                            HomeTab.entries.forEach { t ->
                                NavigationBarItem(
                                    selected = tab == t,
                                    onClick = { if (t == HomeTab.Cart) visits++; tab = t },
                                    icon = {
                                        if (t == HomeTab.Cart && CartState.count > 0) {
                                            BadgedBox(badge = { Badge { Text(CartState.count.toString()) } }) {
                                                Icon(t.icon, t.title)
                                            }
                                        } else Icon(t.icon, t.title)
                                    },
                                    label = { Text(t.title) }
                                )
                            }
                        }
                    }
                ) { padding ->
                    Box(Modifier.padding(padding).consumeWindowInsets(padding)) {
                        when (tab) {
                            HomeTab.Home -> HomeScreen(
                                userName = user?.name,
                                unread = unread,
                                onOpenTab = { tab = it },
                                onLogout = {
                                    OrderStatusWatch.stop(this@MainShellActivity)
                                    session.clear()
                                    startActivity(Intent(this@MainShellActivity, MainActivity::class.java))
                                    finish()
                                },
                                user = user
                            )
                            HomeTab.Search -> SearchScreen()
                            HomeTab.Garage -> GarageScreen()
                            HomeTab.Orders -> OrdersScreen()
                            HomeTab.Cart -> CartScreen(visits)
                        }
                    }
                }
            }
        }
    }

    companion object {
        const val EXTRA_USER_JSON = "extra_user_json"
    }
}

// ---------------- Главная: плитки («бенто») ----------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeScreen(
    userName: String?,
    unread: Int,
    user: UserInfoDto?,
    onOpenTab: (HomeTab) -> Unit,
    onLogout: () -> Unit
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var car by remember { mutableStateOf<GarageCar?>(null) }
    var showProfile by remember { mutableStateOf(false) }
    val server = remember { AppServer(ctx) }
    var finance by remember { mutableStateOf<Finance?>(null) }
    var showTopup by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { if (server.enabled) finance = runCatching { server.finance() }.getOrNull() }

    LaunchedEffect(Unit) { car = runCatching { loadGarage(ctx).firstOrNull() }.getOrNull() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Image(painterResource(R.drawable.logo), "Автодруг", Modifier.height(40.dp).weight(1f, fill = false))
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { ctx.startActivity(Intent(ctx, NotificationsActivity::class.java)) }) {
                BadgedBox(badge = { if (unread > 0) Badge { Text(unread.toString()) } }) {
                    Icon(Icons.Default.Notifications, "Уведомления")
                }
            }
            IconButton(onClick = { showProfile = true }) { Icon(Icons.Default.AccountCircle, "Профиль") }
        }
        Text(
            if (userName.isNullOrBlank()) "Здравствуйте!" else "Здравствуйте, ${userName.substringBefore(' ')}!",
            style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold
        )

        // Одно поле на всё: артикул, VIN, номер кузова или госномер
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Артикул, VIN или госномер") },
            leadingIcon = { Icon(Icons.Default.Search, null) },
            singleLine = true,
            shape = MaterialTheme.shapes.large,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { smartSearch(ctx, query) }),
            modifier = Modifier.fillMaxWidth()
        )

        // Большая плитка — моя машина
        Tile(
            title = car?.title ?: "Моя машина",
            subtitle = if (car != null) "Подобрать запчасти по схемам" else "Добавьте авто по VIN или госномеру",
            icon = Icons.Default.Build,
            accent = true,
            modifier = Modifier.fillMaxWidth().height(120.dp)
        ) { car?.let { openCar(ctx, it) } ?: ctx.startActivity(Intent(ctx, VinSearchActivity::class.java)) }

        // Баланс и уровень цен — с нашего сервера (в клиентском API ABCP их нет)
        finance?.let { f ->
            val owe = f.debt > 0
            Tile(
                title = if (owe) "Долг ${formatRub(f.debt)}" else "Баланс ${formatRub(f.balance)}",
                subtitle = listOfNotNull(
                    f.profile?.let { "Уровень цен: $it" },
                    f.creditLimit.takeIf { it > 0 }?.let { "Кредитный лимит ${formatRub(it)}" },
                    "Пополнить счёт"
                ).joinToString(" · "),
                icon = Icons.Default.AccountBox,
                modifier = Modifier.fillMaxWidth().height(104.dp)
            ) { showTopup = true }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Tile("Подбор по авто", "VIN · кузов · госномер", Icons.Default.Search, modifier = Modifier.weight(1f).height(110.dp)) {
                ctx.startActivity(Intent(ctx, VinSearchActivity::class.java))
            }
            Tile("Спросить менеджера", "Подберём по VIN или фото", Icons.Default.Send, modifier = Modifier.weight(1f).height(110.dp)) {
                ctx.startActivity(Intent(ctx, ChatActivity::class.java))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Tile("Мои заказы", "Статусы и долг", Icons.Default.List, modifier = Modifier.weight(1f).height(96.dp)) { onOpenTab(HomeTab.Orders) }
            Tile("Корзина", if (CartState.count > 0) "${CartState.count} поз." else "Пусто", Icons.Default.ShoppingCart, modifier = Modifier.weight(1f).height(96.dp)) { onOpenTab(HomeTab.Cart) }
        }

        Text("Наши магазины · ${StoreInfo.city}", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text(StoreInfo.hours, style = MaterialTheme.typography.bodySmall)
        StoreInfo.stores.forEach { s -> StoreCard(s) }
        Spacer(Modifier.height(8.dp))
    }

    if (showTopup) {
        TopupSheet(suggested = finance?.debt?.takeIf { it > 0 } ?: 1000.0, onDismiss = { showTopup = false }) { amount ->
            showTopup = false
            scope.launch {
                try {
                    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(server.topupLink(amount))))
                } catch (e: Exception) {
                    android.widget.Toast.makeText(ctx, e.message ?: "Не удалось получить ссылку", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    if (showProfile) {
        ModalBottomSheet(onDismissRequest = { showProfile = false }) {
            Column(Modifier.fillMaxWidth().padding(20.dp).padding(bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(user?.name ?: "Профиль", style = MaterialTheme.typography.titleLarge)
                user?.mobile?.let { Text("Телефон: $it") }
                user?.email?.let { Text("Email: $it") }
                user?.organization?.takeIf { it.isNotBlank() }?.let { Text("Организация: $it") }
                Spacer(Modifier.height(12.dp))
                OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) { Text("Выйти") }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TopupSheet(suggested: Double, onDismiss: () -> Unit, onPay: (Double) -> Unit) {
    var text by remember { mutableStateOf("%.0f".format(suggested)) }
    val amount = text.replace(',', '.').toDoubleOrNull()
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(20.dp).padding(bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Пополнить счёт", style = MaterialTheme.typography.titleLarge)
            OutlinedTextField(
                value = text, onValueChange = { text = it.filter { c -> c.isDigit() || c == ',' || c == '.' } },
                label = { Text("Сумма, ₽") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                modifier = Modifier.fillMaxWidth()
            )
            Button(
                onClick = { amount?.let(onPay) },
                enabled = amount != null && amount >= 1,
                modifier = Modifier.fillMaxWidth().height(52.dp)
            ) { Text("Перейти к оплате") }
        }
    }
}

@Composable
private fun Tile(
    title: String,
    subtitle: String,
    icon: ImageVector,
    modifier: Modifier = Modifier,
    accent: Boolean = false,
    onClick: () -> Unit
) {
    val colors = if (accent) CardDefaults.cardColors(
        containerColor = MaterialTheme.colorScheme.primary,
        contentColor = MaterialTheme.colorScheme.onPrimary
    ) else CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    Card(modifier.clickable(onClick = onClick), colors = colors, shape = MaterialTheme.shapes.large) {
        Column(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.SpaceBetween) {
            Icon(icon, null, tint = if (accent) MaterialTheme.colorScheme.secondary else MaterialTheme.colorScheme.primary)
            Column {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, maxLines = 1)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, maxLines = 2)
            }
        }
    }
}

@Composable
private fun StoreCard(s: Store) {
    val ctx = LocalContext.current
    Card(Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large) {
        Column(Modifier.padding(14.dp)) {
            Text(s.address, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Text(s.phoneDisplay, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilledTonalButton(onClick = {
                    ctx.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${s.phone}")))
                }) { Icon(Icons.Default.Call, null); Spacer(Modifier.width(6.dp)); Text("Позвонить") }
                OutlinedButton(onClick = {
                    val q = Uri.encode("${StoreInfo.city}, ${s.address}")
                    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=$q")))
                }) { Icon(Icons.Default.Place, null); Spacer(Modifier.width(6.dp)); Text("Маршрут") }
            }
        }
    }
}

/** Что ввёл человек: госномер/VIN/кузов → подбор авто, иначе → поиск по артикулу. */
fun smartSearch(ctx: Context, input: String) {
    val q = input.trim()
    if (q.isBlank()) return
    val isVehicle = normalizeRuPlate(q) != null || looksLikeVin(q) || looksLikeFrame(q)
    if (isVehicle) {
        ctx.startActivity(Intent(ctx, VinSearchActivity::class.java).putExtra("prefillVin", q))
    } else {
        ctx.startActivity(Intent(ctx, SearchActivity::class.java).putExtra(SearchActivity.EXTRA_NUMBER, q))
    }
}

/** VIN: 17 символов, без I/O/Q, есть и буквы, и цифры. */
fun looksLikeVin(s: String): Boolean {
    val v = s.uppercase().filter { it.isLetterOrDigit() }
    return v.length == 17 && v.none { it in "IOQ" } && v.any { it.isDigit() } && v.any { it.isLetter() }
}

/** Номер кузова японских авто: «SGL5-400683» — код кузова, дефис, номер. */
fun looksLikeFrame(s: String): Boolean =
    Regex("^[A-Z]{2,4}\\d{0,3}[A-Z]?-\\d{4,7}$").matches(s.trim().uppercase())
