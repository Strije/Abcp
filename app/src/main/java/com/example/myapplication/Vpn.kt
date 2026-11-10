package com.example.myapplication

import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * VPN на телефоне: ABCP, наш сервер и Laximo — в России, через зарубежный VPN они отвечают с перебоями
 * или не отвечают вовсе. Выключить VPN приложение не может — только предупредить и открыть настройки.
 */
object Vpn {
    private var app: Context? = null

    fun init(ctx: Context) { app = ctx.applicationContext }

    /** Сейчас трафик идёт через VPN (по активной сети). Не знаем — считаем, что нет. */
    val active: Boolean
        get() = runCatching {
            val cm = app?.getSystemService(ConnectivityManager::class.java) ?: return false
            cm.getNetworkCapabilities(cm.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        }.getOrDefault(false)

    /** Приписка к ошибкам связи */
    fun hint(): String = if (active) "\nУ вас включён VPN — выключите его: через VPN магазин отвечает с перебоями." else ""

    /** «Понятно» прячет карточку до следующего запуска приложения */
    var dismissed by mutableStateOf(false)
}

/** Карточка на главной, пока включён VPN. Проверяем при каждом возврате на экран (VPN включают и выключают на ходу). */
@Composable
fun VpnWarningCard(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    var on by remember { mutableStateOf(Vpn.active) }
    com.example.myapplication.ui.OnResume { on = Vpn.active }
    if (!on || Vpn.dismissed) return
    LaunchedEffect(Unit) { Analytics.event("vpn_warning") }
    Card(
        modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Включён VPN", fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onErrorContainer)
            Text(
                "Магазин работает в России — через VPN поиск, корзина и заказы могут не загружаться. " +
                    "Для стабильной работы выключите VPN, пока пользуетесь приложением.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    runCatching { ctx.startActivity(Intent(Settings.ACTION_VPN_SETTINGS)) }
                        .onFailure { runCatching { ctx.startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS)) } }
                }) { Text("Настройки VPN") }
                TextButton(onClick = { Vpn.dismissed = true }) { Text("Понятно") }
            }
        }
    }
}
