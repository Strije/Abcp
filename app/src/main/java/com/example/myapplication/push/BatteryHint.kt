package com.example.myapplication.push

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.myapplication.Analytics
import com.example.myapplication.Tips

/**
 * Экономия батареи задерживает push («Заказ готов к выдаче» приходит через полчаса).
 * Один раз — после оформления заказа, когда уведомления человеку реально нужны, — предлагаем снять ограничения.
 */
object BatteryHint {
    private const val KEY = "battery"

    fun isRestricted(ctx: Context): Boolean =
        !(ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName)

    fun shouldShow(ctx: Context) = !Tips.seen(ctx, KEY) && isRestricted(ctx)

    @SuppressLint("BatteryLife")
    fun open(ctx: Context) {
        // Системный вопрос «Разрешить работу в фоне?» — если прошивка его не поддерживает, открываем список настроек
        val ask = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + ctx.packageName))
        runCatching { ctx.startActivity(ask) }.recoverCatching {
            ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName)))
        }
    }

    fun dismiss(ctx: Context) = Tips.markSeen(ctx, KEY)
}

/** Карточка на экране «Заказ оформлен»: один раз, и только если ограничения батареи действительно включены. */
@Composable
fun BatteryHintCard(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    var visible by remember { mutableStateOf(BatteryHint.shouldShow(ctx)) }
    if (!visible) return
    LaunchedEffect(Unit) { BatteryHint.dismiss(ctx); Analytics.event("battery_hint_shown") }
    Card(modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("🔔 Чтобы уведомления приходили сразу", style = MaterialTheme.typography.titleSmall)
            Text(
                "Телефон экономит батарею и может придерживать уведомления — «Заказ готов к выдаче» придёт с опозданием. " +
                    "Разрешите «Автодругу» работать в фоне: заряд это почти не тратит.",
                style = MaterialTheme.typography.bodySmall
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    Analytics.event("battery_hint_allow")
                    BatteryHint.open(ctx)
                    visible = false
                }) { Text("Разрешить") }
                TextButton(onClick = { visible = false }) { Text("Не сейчас") }
            }
        }
    }
}
