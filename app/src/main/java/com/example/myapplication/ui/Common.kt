package com.example.myapplication.ui

import androidx.compose.foundation.layout.*
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
