package com.example.myapplication.abcp

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.AbcpErrorDto
import com.example.myapplication.SessionManager
import com.example.myapplication.server.AppServer
import com.google.gson.Gson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import retrofit2.Response
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Права клиента на API ABCP. Новым клиентам ABCP включает их сам, существующим — менеджер вручную
 * (галочки в карточке клиента). Вход при этом проходит, а поиск и корзина отвечают ошибкой 103.
 * Поэтому после входа пробуем чтение по каждому праву и, если чего-то нет, предлагаем заявку.
 */
object ApiAccess {
    /** Чего не хватает: ключи как на сервере (brands, articles, basket, orders, history). null — ещё не проверяли. */
    var missing by mutableStateOf<List<String>?>(null)
        private set
    var requestedAt by mutableStateOf<Long?>(null)
        private set

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("abcp_session", Context.MODE_PRIVATE) // стирается при выходе

    /**
     * Однажды всё работало — дальше не проверяем совсем (флаг снимет только ответ 103, см. [denied]).
     * Флага нет — пробы (пять запросов к ABCP) сами идут не чаще раза в 6 часов, а между ними карточка
     * показывается по прошлому итогу. force — кнопка «Проверить» на карточке: проверяет всегда.
     */
    suspend fun check(ctx: Context, force: Boolean = false) {
        val p = prefs(ctx)
        if (!force && p.getBoolean(OK, false)) { missing = emptyList(); return }
        if (!force && !probeDue(p.getLong(CHECKED, 0), System.currentTimeMillis())) {
            val last = p.getString(MISSING, null).orEmpty().split(',').filter { it.isNotBlank() }
            if (last.isNotEmpty()) {
                missing = last
                requestedAt = p.getLong(REQUESTED, 0).takeIf { it > 0 }
            }
            return
        }
        val session = SessionManager(ctx)
        val login = session.login()
        val results = runCatching { probe(session) }.getOrNull() ?: return // нет сети — проверим в другой раз
        // Пока шли пробы, вышли из аккаунта — следующему клиенту чужие отметки не оставляем
        if (login.isBlank() || SessionManager(ctx).login() != login) return
        // Запросы дошли до ABCP — время запоминаем при любом ответе (и при сбое: не добивать исчерпанный лимит)
        p.edit().putLong(CHECKED, System.currentTimeMillis()).apply()
        val found = deniedKeys(results)
        if (found.isEmpty()) {
            // Сбой ABCP (5xx и т.п.) — не знаем, есть ли права: флаг и карточку не трогаем, проверим в другой раз
            if (!accessConfirmed(results.values)) return
            missing = found
            p.edit().putBoolean(OK, true).remove(MISSING).apply()
            AbcpShop.guestPrices = false // доступ включили — дальше свои цены
            MemoryCache.history = null // пока прав не было, история запоминалась пустой
            // Была заявка — закрываем, менеджеры увидят «доступ работает»
            if (p.getLong(REQUESTED, 0) > 0) {
                runCatching { AppServer(ctx).accessDone() }
                p.edit().remove(REQUESTED).apply()
                requestedAt = null
            }
        } else {
            missing = found
            p.edit().putString(MISSING, found.joinToString(",")).apply()
            requestedAt = p.getLong(REQUESTED, 0).takeIf { it > 0 }
        }
    }

    /**
     * Какой-то запрос в приложении получил 103 «нет прав», хотя флаг «всё в порядке» записан (права отозвали или
     * включены не все): снимаем флаг и время проверки и проверяем заново — карточка заявки появится снова.
     * Не чаще раза в 6 часов: 103 может идти от метода, которого нет среди проб, и тогда пробы шли бы на каждый отказ.
     */
    @Synchronized
    fun denied(ctx: Context) {
        val app = ctx.applicationContext
        val p = prefs(app)
        if (!p.getBoolean(OK, false)) return // флага нет — карточкой и так управляет проверка раз в 6 часов
        val now = System.currentTimeMillis()
        if (!probeDue(p.getLong(DENIED, 0), now)) return
        p.edit().remove(OK).remove(CHECKED).putLong(DENIED, now).apply()
        scope.launch { check(app) }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    suspend fun request(ctx: Context) {
        com.example.myapplication.Analytics.event("access_request", mapOf("missing" to missing.orEmpty().joinToString(",")))
        AppServer(ctx).accessRequest(missing.orEmpty())
        val now = System.currentTimeMillis()
        prefs(ctx).edit().putLong(REQUESTED, now).apply()
        requestedAt = now
    }

    fun reset() { missing = null; requestedAt = null }

    private suspend fun probe(session: SessionManager): Map<String, Probe> {
        val api = ApiClient.create()
        val l = session.login()
        val p = session.passMd5()
        // Дешёвые запросы на чтение, по одному на каждое право; лимиты тарифа на них — сотни тысяч в сутки
        val out = linkedMapOf<String, Probe>()
        suspend fun check(key: String, call: suspend () -> Response<*>) { out[key] = outcome(call()) }
        check("brands") { api.searchBrands(l, p, "OC90") }
        check("articles") { api.searchArticles(l, p, "OC90", "Knecht") }
        check("basket") { api.basketContent(l, p) }
        check("orders") { api.orders(l, p) }
        check("history") { api.searchHistory(l, p) }
        return out
    }

    private fun outcome(r: Response<*>): Probe =
        if (r.isSuccessful) Probe.Ok else probeOutcome(r.code(), r.errorBody()?.string())

    private const val OK = "api_access_ok"
    private const val REQUESTED = "api_access_requested"
    /** Когда последний раз шли пробы (рядом с флагом; стирается при выходе вместе с сессией) */
    private const val CHECKED = "api_access_checked_at"
    /** Итог последних проб — чего не хватало: по нему карточка показывается между проверками */
    private const val MISSING = "api_access_missing"
    /** Когда последний раз флаг снимали по ответу 103 */
    private const val DENIED = "api_access_denied_at"
}

/** Пробы прав без флага «всё в порядке» — не чаще раза в 6 часов на устройство. */
internal const val PROBE_INTERVAL_MS = 6L * 60 * 60 * 1000

/** Пора ли снова проверять права: с прошлой проверки [lastAt] прошло 6 часов (или её не было, или часы перевели назад). */
internal fun probeDue(lastAt: Long, now: Long): Boolean = now - lastAt !in 0 until PROBE_INTERVAL_MS

/** Итог одной пробы: право есть / права нет / не знаем (сбой сервера). */
internal enum class Probe { Ok, Denied, Unknown }

/** Разбор неуспешного ответа ABCP на пробу. «No results» — запрос принят, значит право есть. */
internal fun probeOutcome(httpCode: Int, errorBody: String?): Probe {
    val err = runCatching { Gson().fromJson(errorBody, AbcpErrorDto::class.java) }.getOrNull()
    return when {
        err?.errorCode == 103 || httpCode == 403 -> Probe.Denied
        err?.errorMessage?.contains("no results", ignoreCase = true) == true -> Probe.Ok
        else -> Probe.Unknown
    }
}

internal fun deniedKeys(results: Map<String, Probe>): List<String> = results.filterValues { it == Probe.Denied }.keys.toList()

/** Флаг «всё в порядке» пишем, только когда каждая проба реально прошла. */
internal fun accessConfirmed(results: Collection<Probe>): Boolean = results.isNotEmpty() && results.all { it == Probe.Ok }

/** Карточка на главной, пока у клиента не включены права. */
@Composable
fun ApiAccessCard() {
    val missing = ApiAccess.missing
    if (missing.isNullOrEmpty()) return
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val requested = ApiAccess.requestedAt

    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Включим поиск и заказы в приложении", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(
                if (requested == null)
                    "Для вашего аккаунта нужно один раз включить доступ из приложения. Отправьте заявку — менеджер включит его в рабочее время."
                else
                    "Заявка отправлена ${SimpleDateFormat("d MMMM, HH:mm", Locale("ru")).format(Date(requested))}. " +
                        "Менеджер включит доступ в рабочее время — после этого нажмите «Проверить».",
                style = MaterialTheme.typography.bodyMedium
            )
            message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (requested == null) Button(enabled = !busy, onClick = {
                    busy = true; message = null
                    scope.launch {
                        try { ApiAccess.request(ctx) } catch (e: Exception) {
                            com.example.myapplication.Analytics.error("Доступ → отправить заявку", e)
                            message = e.message ?: "Не удалось отправить заявку"
                        }
                        busy = false
                    }
                }) { Text(if (busy) "Отправляем…" else "Отправить заявку") }
                OutlinedButton(enabled = !busy, onClick = {
                    busy = true; message = null
                    scope.launch {
                        ApiAccess.check(ctx, force = true)
                        if (!ApiAccess.missing.isNullOrEmpty()) message = "Доступ пока не включён"
                        busy = false
                    }
                }) { Text("Проверить") }
            }
        }
    }
}
