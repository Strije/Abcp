package com.example.myapplication.abcp

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import com.example.myapplication.SessionManager
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.example.myapplication.ui.theme.DeliveryColors
import kotlinx.coroutines.launch

/** «Быстрее» — по сроку: наличие на Хрусталёва/ПОР (самовывоз сегодня) само окажется сверху. */
private enum class Sort(val title: String) { Fast("Быстрее"), Price("Дешевле") }

/** Фильтр по сроку: «Сегодня» — забрать сегодня (свой склад или поставка до конца дня), «До 3 дней». */
private enum class Term(val title: String) { Today("Сегодня"), ThreeDays("До 3 дней") }

private fun Term?.accepts(o: Offer): Boolean = when (this) {
    null -> true
    Term.Today -> o.inStore || formatDelivery(o.deliveryHours) == "сегодня"
    Term.ThreeDays -> o.deliveryHours <= 72
}

private fun Sort.comparator(): Comparator<Offer> = when (this) {
    Sort.Fast -> compareBy<Offer> { it.deliveryHours }.thenBy { it.price }
    Sort.Price -> compareBy<Offer> { it.price }.thenBy { it.deliveryHours }
}

/** Карточка номера, как мобильная выдача сайта: «Наличие» и «Аналоги», сортировка, фото с увеличением. */
@OptIn(ExperimentalMaterial3Api::class)
class OffersActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val brand = intent.getStringExtra(EXTRA_BRAND).orEmpty()
        val number = intent.getStringExtra(EXTRA_NUMBER).orEmpty()
        val description = intent.getStringExtra(EXTRA_DESCRIPTION).orEmpty()
        val shop = AbcpShop(SessionManager(this))
        val server = AppServer(this)

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var offers by remember { mutableStateOf<List<Offer>>(emptyList()) }
                var tab by remember { mutableIntStateOf(0) }
                var tabTouched by remember { mutableStateOf(false) }
                var reload by remember { mutableIntStateOf(0) }
                var sort by remember { mutableStateOf(Sort.Fast) }
                var term by remember { mutableStateOf<Term?>(null) }
                var picked by remember { mutableStateOf<Offer?>(null) }
                var viewer by remember { mutableStateOf<List<String>?>(null) }
                var showAll by remember { mutableStateOf(false) }
                var advices by remember { mutableStateOf<List<BrandHit>>(emptyList()) }

                var reliable by remember { mutableStateOf<Set<String>>(emptySet()) }
                var onlyReliable by remember { mutableStateOf(false) }

                LaunchedEffect(Unit) { if (!shop.isGuest) advices = runCatching { shop.advices(brand, number) }.getOrDefault(emptyList()) }
                // Достоверные аналоги (звёздочка, как на сайте) — с сервера, из кроссов articles/info
                LaunchedEffect(Unit) { if (!shop.isGuest) reliable = runCatching { server.reliable(brand, number) }.getOrDefault(emptySet()) }

                LaunchedEffect(showAll, reload) {
                    loading = true
                    error = null
                    try {
                        offers = shop.offers(number, brand, all = showAll)
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Выдача → загрузка предложений", e)
                        error = e.message ?: "Не удалось загрузить предложения. Проверьте интернет."
                    } finally {
                        loading = false
                    }
                    // Картинки — потом, список уже на экране (articles/info доступен только API-админу, через сервер)
                    val need = offers.filter { it.images.isEmpty() }.map { it.brand to it.number }.distinct()
                    if (need.isNotEmpty() && !shop.isGuest) {
                        val imgs = runCatching { server.images(need) }.getOrDefault(emptyMap())
                        if (imgs.isNotEmpty()) offers = offers.map { o ->
                            if (o.images.isNotEmpty()) o else o.copy(images = imgs["${o.brand}|${o.number}"].orEmpty())
                        }
                    }
                }

                val fix = cleanNumber(number)
                val (own, analogs) = offers.partition {
                    it.numberFix.equals(fix, ignoreCase = true) && it.brand.equals(brand, ignoreCase = true)
                }
                // Самого номера нет, а аналоги есть — сразу показываем аналоги (пока пользователь сам не выбрал вкладку)
                LaunchedEffect(offers) {
                    if (!tabTouched && own.isEmpty() && analogs.isNotEmpty()) tab = 1
                }
                val cmp = sort.comparator()
                // Группы «бренд + номер»: внутри — по выбранной сортировке, сами группы — по лучшему предложению
                fun isReliable(o: Offer) = "${o.brand.uppercase()}|${o.numberFix.uppercase()}" in reliable
                val groups = (if (tab == 0) own else analogs.filter { !onlyReliable || isReliable(it) })
                    .filter { term.accepts(it) }
                    .groupBy { "${it.brand}|${it.numberFix}" }
                    .values.map { it.sortedWith(cmp) }
                    .sortedWith { a, b -> cmp.compare(a.first(), b.first()) }

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Column {
                                    Text("$brand $number", maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    val d = description.ifBlank { own.firstOrNull()?.description.orEmpty() }
                                    if (d.isNotBlank()) Text(d, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                                }
                            },
                            actions = { if (!shop.isGuest) CartIconButton() }
                        )
                    }
                ) { padding ->
                    Column(Modifier.padding(padding).fillMaxSize()) {
                        TabRow(selectedTabIndex = tab) {
                            Tab(tab == 0, { tab = 0; tabTouched = true }, text = { Text("Искомый номер (${own.size})") })
                            Tab(tab == 1, { tab = 1; tabTouched = true }, text = { Text("Аналоги (${analogs.size})") })
                        }
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Sort.entries.forEach { s ->
                                FilterChip(selected = sort == s, onClick = { sort = s }, label = { Text(s.title) })
                            }
                            VerticalDivider(Modifier.height(32.dp).padding(horizontal = 2.dp))
                            Term.entries.forEach { t ->
                                FilterChip(
                                    selected = term == t,
                                    onClick = { term = if (term == t) null else t },
                                    label = { Text(t.title) }
                                )
                            }
                            if (tab == 1 && reliable.isNotEmpty()) {
                                FilterChip(
                                    selected = onlyReliable, onClick = { onlyReliable = !onlyReliable },
                                    label = { Text("★ Только достоверные") }
                                )
                            }
                        }
                        // «Показать все варианты» — старый список остаётся, сверху полоска загрузки
                        if (loading && offers.isNotEmpty()) LinearProgressIndicator(Modifier.fillMaxWidth())
                        when {
                            loading && offers.isEmpty() -> Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
                            error != null && offers.isEmpty() -> com.example.myapplication.ui.ErrorState(error!!, onRetry = { reload++ })
                            groups.isEmpty() -> Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text(
                                    when {
                                        term != null -> "С условием «${term!!.title}» ничего нет — снимите фильтр."
                                        tab == 0 -> "По самому номеру предложений нет — посмотрите аналоги."
                                        else -> "Аналогов не найдено"
                                    }
                                )
                                if (!showAll) OutlinedButton(onClick = { showAll = true }) { Text("Показать все варианты") }
                                if (advices.isNotEmpty()) AdvicesBlock(advices)
                            }
                            else -> LazyColumn(
                                Modifier.fillMaxSize(),
                                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 16.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                items(groups, key = { it.first().brand + it.first().numberFix }) { list ->
                                    ArticleCard(list, reliable = tab == 1 && isReliable(list.first()), onImage = { viewer = it }, onPick = { picked = it })
                                }
                                if (!showAll) item {
                                    // ABCP по умолчанию отдаёт сокращённую выдачу — как сайт до «Показать все варианты»
                                    OutlinedButton(onClick = { showAll = true }, modifier = Modifier.fillMaxWidth()) {
                                        Text("Показать все варианты")
                                    }
                                }
                                if (advices.isNotEmpty()) item { AdvicesBlock(advices) }
                            }
                        }
                    }
                }

                picked?.let { o ->
                    AddToCartSheet(
                        offer = o,
                        onImage = { viewer = it },
                        onDismiss = { picked = null },
                        onConfirm = { qty ->
                            picked = null
                            if (shop.isGuest) {
                                Toast.makeText(this@OffersActivity, "Войдите, чтобы положить в корзину", Toast.LENGTH_LONG).show()
                                startActivity(android.content.Intent(this@OffersActivity, com.example.myapplication.MainActivity::class.java))
                                return@AddToCartSheet
                            }
                            scope.launch {
                                try {
                                    shop.addToBasket(o, qty)
                                    CartState.refresh(shop)
                                    Toast.makeText(this@OffersActivity, "Добавлено в корзину", Toast.LENGTH_SHORT).show()
                                } catch (e: Exception) {
                                    com.example.myapplication.Analytics.error("Выдача → в корзину", e)
                                    Toast.makeText(this@OffersActivity, e.message ?: "Ошибка", Toast.LENGTH_LONG).show()
                                }
                            }
                        }
                    )
                }

                viewer?.let { urls -> ImageViewer(urls) { viewer = null } }
            }
        }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
        const val EXTRA_DESCRIPTION = "extra_description"
    }
}

/** Артикул: фото, номер, бренд, описание и под ним все предложения. */
@Composable
private fun ArticleCard(list: List<Offer>, reliable: Boolean = false, onImage: (List<String>) -> Unit, onPick: (Offer) -> Unit) {
    val head = list.first()
    val images = list.flatMap { it.images }.distinct()
    ElevatedCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (images.isNotEmpty()) {
                AsyncImage(
                    model = images.first(),
                    contentDescription = "Фото",
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.size(64.dp).clip(RoundedCornerShape(10.dp))
                        .background(Color.White).clickable { onImage(images) }
                )
                Spacer(Modifier.width(12.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(head.number, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(head.brand, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
                    if (reliable) {
                        Text(" ★", color = Color(0xFFF2B01E), style = MaterialTheme.typography.bodyMedium)
                        Text(" достоверный аналог", style = MaterialTheme.typography.labelSmall, color = DeliveryColors.later)
                    }
                }
                if (head.description.isNotBlank()) {
                    Text(head.description, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                // Своих фото нет (ABCP даёт их только через articles/info с лимитом 10 в сутки) —
                // даём посмотреть фото детали в поиске картинок
                if (images.isEmpty()) PhotoSearchLinks(head.brand, head.number)
            }
        }
        list.forEach { o ->
            HorizontalDivider()
            OfferRow(o) { onPick(o) }
        }
    }
}

@Composable
private fun OfferRow(o: Offer, onClick: () -> Unit) {
    val stripe = parseAbcpColor(o.supplierColor) ?: Color.Transparent
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).clickable(onClick = onClick)) {
        // Цвет поставщика, как фон строки на сайте — здесь узкой полосой слева
        Box(Modifier.width(5.dp).fillMaxHeight().background(stripe))
        Column(Modifier.weight(1f).padding(start = 10.dp, top = 10.dp, bottom = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    o.deliveryText(),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = if (o.inStore) FontWeight.Bold else FontWeight.Normal,
                    color = when {
                        o.inStore -> DeliveryColors.today
                        o.deliveryHours <= 72 -> DeliveryColors.soon
                        else -> DeliveryColors.later
                    },
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false)
                )
            }
            val pack = if (o.packing > 1) " · партия ${o.packing} шт." else ""
            Text(formatAvailability(o.availability) + pack, style = MaterialTheme.typography.bodySmall)
            o.probability?.let { p ->
                Text(
                    "Вероятность поставки $p%",
                    style = MaterialTheme.typography.bodySmall,
                    color = when {
                        p >= 90 -> DeliveryColors.today
                        p >= 70 -> DeliveryColors.soon
                        else -> MaterialTheme.colorScheme.error
                    }
                )
            }
            if (o.badges.isNotEmpty() || o.noReturn || o.isUsed) Badges(o)
        }
        Column(
            Modifier.padding(10.dp).align(Alignment.CenterVertically),
            horizontalAlignment = Alignment.End
        ) {
            Text(formatRub(o.price), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            FilledTonalIconButton(onClick = onClick, modifier = Modifier.size(36.dp)) {
                Icon(Icons.Default.ShoppingCart, "В корзину", Modifier.size(18.dp))
            }
        }
    }
}

/** Метки поставщика цветом: надёжный — зелёная, сторонний склад/без возврата — красная. */
@Composable
private fun Badges(o: Offer) {
    val list = buildList {
        if (o.isUsed) add(SupplierBadge("Б/у", BadgeKind.Bad))
        addAll(o.badges)
        if (o.badges.isEmpty() && o.noReturn) add(SupplierBadge("Возврат невозможен", BadgeKind.Bad))
    }
    Row(Modifier.padding(top = 4.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        list.forEach { b ->
            val c = when (b.kind) {
                BadgeKind.Good -> DeliveryColors.today
                BadgeKind.Bad -> MaterialTheme.colorScheme.error
                BadgeKind.Info -> DeliveryColors.later
            }
            Text(
                b.text, style = MaterialTheme.typography.labelSmall, color = c, maxLines = 1,
                modifier = Modifier.background(c.copy(alpha = 0.12f), RoundedCornerShape(6.dp)).padding(horizontal = 6.dp, vertical = 2.dp)
            )
        }
    }
}

/** «С этим товаром покупают» — статистика заказов магазина; нажатие открывает цены. */
@Composable
private fun AdvicesBlock(list: List<BrandHit>) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("С этим товаром покупают", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            list.forEach { h ->
                OutlinedCard(
                    Modifier.width(170.dp).clickable {
                        ctx.startActivity(
                            android.content.Intent(ctx, OffersActivity::class.java)
                                .putExtra(OffersActivity.EXTRA_BRAND, h.brand)
                                .putExtra(OffersActivity.EXTRA_NUMBER, h.number)
                                .putExtra(OffersActivity.EXTRA_DESCRIPTION, h.description)
                        )
                    }
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(h.number, fontWeight = FontWeight.Bold, maxLines = 1)
                        Text(h.brand, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary, maxLines = 1)
                        if (h.description.isNotBlank()) {
                            Text(h.description, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
}

/** Шторка снизу: фото, срок, метки, количество с учётом кратности и итог. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddToCartSheet(offer: Offer, onImage: (List<String>) -> Unit, onDismiss: () -> Unit, onConfirm: (Int) -> Unit) {
    val step = offer.packing
    val max = if (offer.availability > 0) offer.availability else Int.MAX_VALUE
    var qty by remember { mutableIntStateOf(step) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (offer.images.isNotEmpty()) {
                    AsyncImage(
                        model = offer.images.first(), contentDescription = "Фото", contentScale = ContentScale.Fit,
                        modifier = Modifier.size(80.dp).clip(RoundedCornerShape(12.dp)).background(Color.White)
                            .clickable { onImage(offer.images) }
                    )
                    Spacer(Modifier.width(12.dp))
                }
                Column {
                    Text("${offer.brand} ${offer.number}", style = MaterialTheme.typography.titleLarge)
                    if (offer.description.isNotBlank()) Text(offer.description, style = MaterialTheme.typography.bodyMedium)
                }
            }
            Text("${formatRub(offer.price)} · ${offer.deliveryText()}", style = MaterialTheme.typography.titleMedium)
            Text(formatAvailability(offer.availability), style = MaterialTheme.typography.bodySmall)
            offer.probability?.let { p ->
                Text("Вероятность поставки $p%" + (offer.probabilityText?.let { " — $it" } ?: ""), style = MaterialTheme.typography.bodySmall)
            }
            offer.updatedAt?.let { Text("Прайс обновлён: $it", style = MaterialTheme.typography.bodySmall) }
            if (offer.badges.isNotEmpty() || offer.noReturn || offer.isUsed) Badges(offer)
            Row(verticalAlignment = Alignment.CenterVertically) {
                FilledTonalButton(onClick = { qty = (qty - step).coerceAtLeast(step) }) { Text("−") }
                Text("$qty шт.", Modifier.padding(horizontal = 20.dp), style = MaterialTheme.typography.titleLarge)
                FilledTonalButton(onClick = { if (qty + step <= max) qty += step }) { Text("+") }
            }
            Button(onClick = { onConfirm(qty) }, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                Text("В корзину · ${formatRub(offer.price * qty)}")
            }
        }
    }
}

/** Фото на весь экран: листать пальцем, приближать щипком; пока не приближено — свайп листает фото. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun ImageViewer(urls: List<String>, onClose: () -> Unit) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            val pager = rememberPagerState { urls.size }
            HorizontalPager(state = pager, modifier = Modifier.fillMaxSize()) { page ->
                var scale by remember(page) { mutableFloatStateOf(1f) }
                var offset by remember(page) { mutableStateOf(androidx.compose.ui.geometry.Offset.Zero) }
                val state = rememberTransformableState { zoom, pan, _ ->
                    scale = (scale * zoom).coerceIn(1f, 5f)
                    offset = if (scale == 1f) androidx.compose.ui.geometry.Offset.Zero else offset + pan
                }
                AsyncImage(
                    model = urls[page], contentDescription = null, contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                        .transformable(state, canPan = { scale > 1f })
                        .graphicsLayer { scaleX = scale; scaleY = scale; translationX = offset.x; translationY = offset.y }
                )
            }
            if (urls.size > 1) {
                Text(
                    "${pager.currentPage + 1} / ${urls.size}", color = Color.White,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(24.dp)
                )
            }
            IconButton(onClick = onClose, modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                Icon(Icons.Default.Close, "Закрыть", tint = Color.White)
            }
        }
    }
}

/** «Фото в Яндексе / Google»: поиск картинок по бренду и номеру, открывается в браузере. */
@Composable
fun PhotoSearchLinks(brand: String, number: String) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val q = android.net.Uri.encode("$brand $number".trim())
    fun open(url: String) = ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)))
    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilledTonalButton(
            onClick = { open("https://yandex.ru/images/search?text=$q") },
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            modifier = Modifier.height(34.dp)
        ) { Text("📷 Фото в Яндексе", style = MaterialTheme.typography.labelMedium) }
        FilledTonalButton(
            onClick = { open("https://www.google.com/search?tbm=isch&q=$q") },
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            modifier = Modifier.height(34.dp)
        ) { Text("📷 Фото в Google", style = MaterialTheme.typography.labelMedium) }
    }
}
