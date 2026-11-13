package com.example.myapplication.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Search
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver

/** Ошибка загрузки с кнопкой «Повторить» — вместо голого красного текста. */
@Composable
fun ErrorState(message: String, modifier: Modifier = Modifier, onRetry: (() -> Unit)? = null) {
    Column(
        modifier.fillMaxWidth().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text(message, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        if (onRetry != null) OutlinedButton(onClick = onRetry) { Text("Повторить") }
    }
}

/**
 * Долгое ожидание (поиск у поставщиков идёт до минуты): полоска, подпись, которая меняется со временем — видно,
 * что приложение не зависло, — и «Отменить». Доли готовности нет: ABCP отвечает одним куском в самом конце.
 */
@Composable
fun WaitingState(modifier: Modifier = Modifier, first: String = "Ищем…", onCancel: (() -> Unit)? = null) {
    var seconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) { while (true) { kotlinx.coroutines.delay(1000); seconds++ } }
    Column(
        modifier.fillMaxWidth().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        LinearProgressIndicator(Modifier.fillMaxWidth())
        Text(waitingText(seconds, first), textAlign = TextAlign.Center)
        if (onCancel != null && seconds >= 8) TextButton(onClick = onCancel) { Text("Отменить") }
    }
}

/** Что писать под полоской на какой секунде ожидания. */
internal fun waitingText(seconds: Int, first: String = "Ищем…"): String = when {
    seconds < 4 -> first
    seconds < 15 -> "Спрашиваем поставщиков…"
    seconds < 40 -> "Некоторые поставщики отвечают до минуты — ждём их цены…"
    else -> "Почти готово. Поставщики отвечают дольше обычного…"
}

/** Пустой список: текст по центру и необязательное действие. Прокручивается — чтобы работал «потянуть вниз». */
@Composable
fun EmptyState(text: String, action: String? = null, onAction: (() -> Unit)? = null) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically)
    ) {
        Text(text, textAlign = TextAlign.Center)
        if (action != null && onAction != null) Button(onClick = onAction) { Text(action) }
    }
}

/**
 * Экран-список со всеми состояниями: первая загрузка — спиннер по центру, дальше старые данные
 * остаются на экране, а обновление идёт тихо (индикатор «потянуть вниз»).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RefreshableContent(
    hasData: Boolean,
    loading: Boolean,
    error: String?,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit
) {
    PullToRefreshBox(isRefreshing = loading && hasData, onRefresh = onRefresh, modifier = modifier.fillMaxSize()) {
        when {
            !hasData && loading -> Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
            !hasData && error != null -> Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                ErrorState(error, Modifier.align(Alignment.Center), onRetry = onRefresh)
            }
            else -> content()
        }
    }
}

/** Действие при каждом возврате на экран (после оформления, добавления в гараж и т.п.). */
@Composable
fun OnResume(block: () -> Unit) {
    val owner = LocalLifecycleOwner.current
    val current by rememberUpdatedState(block)
    DisposableEffect(owner) {
        var first = true
        val obs = LifecycleEventObserver { _, e ->
            if (e == Lifecycle.Event.ON_RESUME) {
                // Первый ON_RESUME совпадает с открытием экрана — его грузит сам экран
                if (first) first = false else current()
            }
        }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
}

/** 🔍 в шапке экрана — к поиску запчастей откуда угодно (корзина, заказы, уведомления). */
@androidx.compose.runtime.Composable
fun SearchAction() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    androidx.compose.material3.IconButton(onClick = {
        ctx.startActivity(android.content.Intent(ctx, com.example.myapplication.SearchActivity::class.java))
    }) { androidx.compose.material3.Icon(Icons.Default.Search, "Поиск запчастей") }
}

/**
 * ← в шапке экрана: системный жест «назад» находят не все. На вкладках главного экрана не показываем —
 * там нижнее меню, а «назад» закрыл бы приложение.
 */
@Composable
fun BackButton() {
    val activity = androidx.compose.ui.platform.LocalContext.current as? androidx.activity.ComponentActivity ?: return
    if (activity is com.example.myapplication.MainShellActivity) return
    IconButton(onClick = { activity.onBackPressedDispatcher.onBackPressed() }) {
        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад")
    }
}

/**
 * Открыть внешнее приложение (браузер, карты). Нужного приложения нет — не падаем, а говорим об этом
 * (AppMetrica: вылет на планшете без звонилки).
 */
fun openExternal(ctx: android.content.Context, intent: android.content.Intent) {
    try {
        ctx.startActivity(intent)
    } catch (_: android.content.ActivityNotFoundException) {
        android.widget.Toast.makeText(ctx, "На устройстве нет приложения, чтобы открыть это", android.widget.Toast.LENGTH_LONG).show()
    }
}

/** Позвонить; на планшете без звонков — номер в буфер обмена. */
fun dial(ctx: android.content.Context, phone: String) {
    try {
        ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_DIAL, android.net.Uri.parse("tel:$phone")))
    } catch (_: android.content.ActivityNotFoundException) {
        ctx.getSystemService(android.content.ClipboardManager::class.java)
            ?.setPrimaryClip(android.content.ClipData.newPlainText("Телефон", phone))
        android.widget.Toast.makeText(ctx, "Звонки на этом устройстве недоступны. Номер $phone скопирован", android.widget.Toast.LENGTH_LONG).show()
    }
}

/** Открыть цены и аналоги по номеру (из корзины, заказа) — сразу выдача, без выбора бренда. */
fun openOffers(ctx: android.content.Context, brand: String, number: String, description: String = "") {
    if (number.isBlank()) return
    ctx.startActivity(
        android.content.Intent(ctx, com.example.myapplication.abcp.OffersActivity::class.java)
            .putExtra(com.example.myapplication.abcp.OffersActivity.EXTRA_BRAND, brand)
            .putExtra(com.example.myapplication.abcp.OffersActivity.EXTRA_NUMBER, number)
            .putExtra(com.example.myapplication.abcp.OffersActivity.EXTRA_DESCRIPTION, description)
    )
}
