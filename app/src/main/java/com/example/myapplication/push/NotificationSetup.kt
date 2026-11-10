package com.example.myapplication.push

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationManagerCompat
import com.example.myapplication.Analytics
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.launch

/**
 * Почему уведомления опаздывают или не приходят, и что с этим сделать на конкретном телефоне.
 * Прошивки Xiaomi/POCO, Honor/Huawei, Tecno/Infinix и др. «усыпляют» приложения сверх стандартного Android:
 * мало разрешить уведомления — нужны автозапуск и работа в фоне, причём и для RuStore (push идёт через него).
 */
object PhoneMaker {
    /** Семейство прошивки по производителю: от него зависят названия настроек и куда вести человека. */
    enum class Family(val title: String) {
        Xiaomi("Xiaomi / Redmi / POCO"), Honor("Honor / Huawei"), Samsung("Samsung"),
        Transsion("Tecno / Infinix"), Oppo("Realme / OPPO / OnePlus"), Vivo("Vivo"), Other("Android")
    }

    val family: Family = Build.MANUFACTURER.lowercase().let { m ->
        when {
            m in setOf("xiaomi", "redmi", "poco") -> Family.Xiaomi
            m in setOf("honor", "huawei") -> Family.Honor
            m == "samsung" -> Family.Samsung
            m in setOf("tecno", "infinix", "itel") -> Family.Transsion
            m in setOf("oppo", "realme", "oneplus") -> Family.Oppo
            m in setOf("vivo", "iqoo") -> Family.Vivo
            else -> Family.Other
        }
    }

    /** Где в прошивке автозапуск: экраны разных версий прошивок, пробуем по очереди. */
    private val autostart: Map<Family, List<ComponentName>> = mapOf(
        Family.Xiaomi to listOf(
            ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")
        ),
        Family.Honor to listOf(
            ComponentName("com.hihonor.systemmanager", "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity"),
            ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"),
            ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity")
        ),
        Family.Transsion to listOf(
            ComponentName("com.transsion.phonemaster", "com.cyin.himgr.autostart.AutoStartActivity")
        ),
        Family.Oppo to listOf(
            ComponentName("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"),
            ComponentName("com.coloros.safecenter", "com.coloros.safecenter.startupapp.StartupAppListActivity"),
            ComponentName("com.oplus.safecenter", "com.oplus.safecenter.permission.startup.StartupAppListActivity")
        ),
        Family.Vivo to listOf(
            ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"),
            ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager")
        ),
        Family.Samsung to listOf(
            ComponentName("com.samsung.android.lool", "com.samsung.android.sm.battery.ui.BatteryActivity")
        )
    )

    /** Что нажать в прошивке — словами, как это называется на экране телефона. */
    fun autostartHint(): String? = when (family) {
        Family.Xiaomi -> "Включите «Автозапуск» для Автодруга и RuStore. Затем в настройках приложения: " +
            "«Контроль активности» (или «Батарея») → «Нет ограничений»."
        Family.Honor -> "«Запуск приложений» → Автодруг → снимите «Управлять автоматически» и включите все три переключателя. " +
            "То же — для RuStore."
        Family.Transsion -> "Phone Master → «Автозапуск» → включите Автодруг и RuStore. " +
            "Ещё: Настройки → Батарея → «Фоновая активность» → разрешить."
        Family.Oppo -> "«Автозапуск» → включите Автодруг и RuStore. В настройках приложения: «Батарея» → «Разрешить фоновую активность»."
        Family.Vivo -> "«Фоновая активность / Автозапуск» → включите Автодруг и RuStore. В «Батарея» → «Высокое фоновое энергопотребление» → разрешить."
        Family.Samsung -> "Батарея → «Ограничения фоновой работы» → уберите Автодруг и RuStore из «Спящих приложений» " +
            "и добавьте в «Никогда не переводить в спящий режим»."
        Family.Other -> null
    }

    /** Открыть экран автозапуска; не нашли (другая версия прошивки) — настройки приложения. */
    fun openAutostart(ctx: Context) {
        for (c in autostart[family].orEmpty()) {
            if (runCatching { ctx.startActivity(Intent().setComponent(c).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess) return
        }
        openAppDetails(ctx, ctx.packageName)
    }

    fun openAppDetails(ctx: Context, pkg: String) {
        runCatching { ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$pkg"))) }
    }

    fun openNotificationSettings(ctx: Context) {
        val i = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName)
        else Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName))
        runCatching { ctx.startActivity(i) }.onFailure { openAppDetails(ctx, ctx.packageName) }
    }

    /** Push RuStore приходит через приложение RuStore — его тоже нельзя усыплять. */
    const val RUSTORE = "ru.vk.store"

    fun installed(ctx: Context, pkg: String) = runCatching { ctx.packageManager.getPackageInfo(pkg, 0) }.isSuccess
}

class NotificationSetupActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { NotificationSetupScreen() } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NotificationSetupScreen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    fun enabled() = NotificationManagerCompat.from(ctx).areNotificationsEnabled()
    var notifOn by remember { mutableStateOf(enabled()) }
    var restricted by remember { mutableStateOf(BatteryHint.isRestricted(ctx)) }
    val rustore = remember { PhoneMaker.installed(ctx, PhoneMaker.RUSTORE) }
    // Вернулся из настроек — перечитываем, что изменилось
    com.example.myapplication.ui.OnResume { notifOn = enabled(); restricted = BatteryHint.isRestricted(ctx) }
    LaunchedEffect(Unit) {
        Analytics.event("notif_setup_open", mapOf(
            "maker" to Build.MANUFACTURER, "model" to Build.MODEL, "notifications" to notifOn,
            "battery_restricted" to restricted, "rustore" to rustore
        ))
    }
    var testState by remember { mutableStateOf<String?>(null) }
    var testSent by remember { mutableStateOf(false) }

    Scaffold(topBar = {
        TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Уведомления вовремя") })
    }) { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                "Телефоны ${PhoneMaker.family.title} экономят заряд и могут придерживать уведомления — «Заказ готов» " +
                    "придёт с опозданием. Пройдите шаги ниже, заряд это почти не тратит.",
                style = MaterialTheme.typography.bodyMedium
            )

            Step(1, "Уведомления разрешены", done = notifOn,
                text = "Без этого телефон не покажет ни одного уведомления.",
                action = "Включить") { PhoneMaker.openNotificationSettings(ctx) }

            Step(2, "Работа в фоне без ограничений", done = !restricted,
                text = "Android не будет «усыплять» Автодруг.",
                action = "Разрешить") { Analytics.event("battery_hint_allow"); BatteryHint.open(ctx) }

            PhoneMaker.autostartHint()?.let { hint ->
                Step(3, "Автозапуск и фон в прошивке", done = null, text = hint, action = "Открыть настройки") {
                    Analytics.event("notif_setup_autostart", mapOf("maker" to Build.MANUFACTURER))
                    PhoneMaker.openAutostart(ctx)
                }
            }

            if (rustore) Step(4, "RuStore тоже не усыплять", done = null,
                text = "Уведомления приходят через приложение RuStore. Откройте его настройки: «Батарея» → без ограничений, " +
                    "включите автозапуск, если он есть.",
                action = "Настройки RuStore") { PhoneMaker.openAppDetails(ctx, PhoneMaker.RUSTORE) }

            HorizontalDivider()
            Text("Проверка", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(
                "Нажмите кнопку и заблокируйте экран. Через 20 секунд придёт уведомление — так же, как приходит статус заказа.",
                style = MaterialTheme.typography.bodySmall
            )
            Button(
                enabled = testState != "sending",
                onClick = {
                    testState = "sending"
                    scope.launch {
                        testState = try {
                            AppServer(ctx).pushSelfTest(20)
                            Analytics.event("push_selftest", mapOf("maker" to Build.MANUFACTURER, "model" to Build.MODEL))
                            testSent = true
                            "Отправим через 20 секунд — заблокируйте экран и ждите."
                        } catch (e: Exception) {
                            e.message ?: "Не удалось отправить проверку"
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) { Text(if (testState == "sending") "Отправляем…" else "Прислать проверочное уведомление") }
            testState?.takeIf { it != "sending" }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            // Ответ человека — по нему видно, на каких телефонах push не доходит (AppMetrica: push_selftest_result)
            if (testSent) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Пришло?", Modifier.weight(1f))
                OutlinedButton(onClick = { report(true, notifOn, restricted); testSent = false; testState = "Отлично, всё настроено 👍" }) { Text("Да") }
                OutlinedButton(onClick = {
                    report(false, notifOn, restricted); testSent = false
                    testState = "Пройдите шаги выше ещё раз. Не помогло — напишите менеджеру в чат, разберёмся."
                }) { Text("Нет") }
            }
        }
    }
}

private fun report(arrived: Boolean, notifOn: Boolean, restricted: Boolean) =
    Analytics.event("push_selftest_result", mapOf(
        "arrived" to arrived, "maker" to Build.MANUFACTURER, "model" to Build.MODEL,
        "notifications" to notifOn, "battery_restricted" to restricted
    ))

/** Шаг настройки: done=null — проверить нельзя (настройки прошивки нам не видны), только подсказка и кнопка. */
@Composable
private fun Step(n: Int, title: String, done: Boolean?, text: String, action: String, onAction: () -> Unit) {
    val ok = done == true
    ElevatedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                (if (ok) "✅ " else "$n. ") + title,
                style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold
            )
            if (!ok) {
                Text(text, style = MaterialTheme.typography.bodySmall)
                FilledTonalButton(onClick = onAction) { Text(action) }
            }
        }
    }
}
