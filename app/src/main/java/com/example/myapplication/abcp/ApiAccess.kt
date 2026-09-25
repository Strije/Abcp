package com.example.myapplication.abcp

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.SessionManager
import com.example.myapplication.server.AppServer
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

    /** Однажды всё работало — дальше не проверяем (права у клиента не отзывают). */
    suspend fun check(ctx: Context, force: Boolean = false) {
        val p = prefs(ctx)
        if (!force && p.getBoolean(OK, false)) { missing = emptyList(); return }
        val found = runCatching { probe(SessionManager(ctx)) }.getOrNull() ?: return // нет сети — проверим в другой раз
        missing = found
        if (found.isEmpty()) {
            p.edit().putBoolean(OK, true).apply()
            // Была заявка — закрываем, менеджеры увидят «доступ работает»
            if (p.getLong(REQUESTED, 0) > 0) {
                runCatching { AppServer(ctx).accessDone() }
                p.edit().remove(REQUESTED).apply()
                requestedAt = null
            }
        } else {
            requestedAt = p.getLong(REQUESTED, 0).takeIf { it > 0 }
        }
    }

    suspend fun request(ctx: Context) {
        com.example.myapplication.Analytics.event("access_request", mapOf("missing" to missing.orEmpty().joinToString(",")))
        AppServer(ctx).accessRequest(missing.orEmpty())
        val now = System.currentTimeMillis()
        prefs(ctx).edit().putLong(REQUESTED, now).apply()
        requestedAt = now
    }

    fun reset() { missing = null; requestedAt = null }

    private suspend fun probe(session: SessionManager): List<String> {
        val api = ApiClient.create()
        val l = session.login()
        val p = session.passMd5()
        // Дешёвые запросы на чтение, по одному на каждое право; лимиты тарифа на них — сотни тысяч в сутки
        val out = mutableListOf<String>()
        suspend fun check(key: String, call: suspend () -> Response<*>) { if (isDenied(call())) out += key }
        check("brands") { api.searchBrands(l, p, "OC90") }
        check("articles") { api.searchArticles(l, p, "OC90", "Knecht") }
        check("basket") { api.basketContent(l, p) }
        check("orders") { api.orders(l, p) }
        check("history") { api.searchHistory(l, p) }
        return out
    }

    private fun isDenied(r: Response<*>): Boolean {
        if (r.isSuccessful) return false
        val code = abcpErrorCode(r.errorBody()?.string())
        return code == 103 || r.code() == 403
    }

    private const val OK = "api_access_ok"
    private const val REQUESTED = "api_access_requested"
}

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
