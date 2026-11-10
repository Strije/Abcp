package com.example.myapplication

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import com.example.myapplication.abcp.OffersActivity
import com.example.myapplication.abcp.OrderDetailsActivity
import java.net.URI
import java.net.URLDecoder

/**
 * Ссылки внутрь приложения (11.2026): из рассылок, с сайта, из чата.
 *   avtodrug92://search?q=OC90                  — поиск по артикулу
 *   avtodrug92://offers?brand=MAHLE&number=OC90 — сразу выдача
 *   avtodrug92://order/12345                    — заказ (нужен вход)
 *   avtodrug92://chat                           — чат с магазином
 *   avtodrug92://news/2                         — новость avtodrug92.ru/news2
 *   avtodrug92://vin?q=XTA21099…                — подбор по VIN / госномеру
 *   https://…                                   — в браузере (avtodrug92.ru/newsN — новостью в приложении)
 */
sealed class DeepLink {
    data class Search(val query: String) : DeepLink()
    data class Offers(val brand: String, val number: String) : DeepLink()
    data class Order(val number: String) : DeepLink()
    data object Chat : DeepLink()
    data class News(val page: Int) : DeepLink()
    data class Vin(val query: String) : DeepLink()
    data class Web(val url: String) : DeepLink()

    companion object {
        const val SCHEME = "avtodrug92"
        /** Ссылка в extras: так её кладёт push (поле data «link») и RuStore при нажатии на своё уведомление */
        const val EXTRA_LINK = "link"

        /** Разбор ссылки; чужая схема, пустые параметры, мусор — null (тогда просто открываем приложение). */
        fun parse(raw: String?): DeepLink? {
            val s = raw?.trim()?.takeIf { it.isNotEmpty() && it.length <= 2000 } ?: return null
            val uri = runCatching { URI(s.replace(" ", "%20")) }.getOrNull() ?: return null
            return when (uri.scheme?.lowercase()) {
                SCHEME -> app(uri)
                "https" -> web(uri, s)
                else -> null
            }
        }

        private fun app(uri: URI): DeepLink? {
            // avtodrug92://order/1 → host «order», путь «/1»; avtodrug92:order/1 (без //) тоже принимаем
            val parts = ((uri.host ?: uri.rawAuthority).orEmpty() + "/" + (uri.rawPath ?: uri.rawSchemeSpecificPart?.substringBefore('?')).orEmpty())
                .split('/').filter { it.isNotBlank() }.map { decode(it) }
            val q = query(uri.rawQuery ?: uri.rawSchemeSpecificPart?.substringAfter('?', ""))
            fun p(name: String) = q[name]?.trim()?.takeIf { it.isNotEmpty() }
            return when (parts.firstOrNull()?.lowercase()) {
                "search" -> (p("q") ?: p("number") ?: parts.getOrNull(1))?.let { Search(it) }
                "offers" -> {
                    val number = p("number") ?: return null
                    val brand = p("brand")
                    if (brand != null) Offers(brand, number) else Search(number)
                }
                "order" -> (parts.getOrNull(1) ?: p("number"))?.trim()
                    ?.takeIf { it.matches(Regex("[\\w-]{1,30}")) }?.let { Order(it) }
                "chat" -> Chat
                "news" -> (parts.getOrNull(1) ?: p("n"))?.toIntOrNull()?.takeIf { it in 1..99 }?.let { News(it) }
                "vin", "car" -> Vin(p("q") ?: p("vin") ?: "")
                else -> null
            }
        }

        private fun web(uri: URI, raw: String): DeepLink? {
            val host = uri.host?.lowercase() ?: return null
            if (host == "avtodrug92.ru" || host == "www.avtodrug92.ru") {
                Regex("/news(\\d{1,2})/?").matchEntire(uri.rawPath.orEmpty())?.let { m ->
                    return News(m.groupValues[1].toInt()).takeIf { it.page >= 1 }
                }
            }
            return Web(raw)
        }

        private fun query(raw: String?): Map<String, String> =
            raw.orEmpty().split('&').filter { it.isNotEmpty() }.associate {
                val (k, v) = it.split('=', limit = 2).let { kv -> kv[0] to kv.getOrElse(1) { "" } }
                decode(k).lowercase() to decode(v)
            }

        private fun decode(s: String): String = runCatching { URLDecoder.decode(s, "UTF-8") }.getOrDefault(s)

        /** Нажатие на уведомление со ссылкой: открыть её через точку входа. */
        fun intent(ctx: Context, link: String): Intent =
            Intent(ctx, DeepLinkActivity::class.java).setData(Uri.parse(link.trim()))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        /** Ссылка из intent: data (avtodrug92://…) или extras «link» (push). */
        fun from(intent: Intent?): String? =
            intent?.dataString?.takeIf { parse(it) != null } ?: intent?.getStringExtra(EXTRA_LINK)?.takeIf { parse(it) != null }

        /**
         * Открыть экран по ссылке. withHome — приложение ещё не запущено: под экраном кладём главную,
         * чтобы «назад» вела в приложение, а не на рабочий стол. Гостю заказ не открыть — экран входа.
         */
        fun open(activity: Activity, link: DeepLink?, withHome: Boolean) {
            val ctx = activity
            val session = SessionManager(ctx)
            val loggedIn = session.isLoggedIn()
            val login = Intent(ctx, MainActivity::class.java)
            val home = if (loggedIn) Intent(ctx, MainShellActivity::class.java)
                .putExtra(MainShellActivity.EXTRA_USER_JSON, session.userJson()) else login
            val target: Intent? = when (link) {
                null -> null
                is Search -> Intent(ctx, SearchActivity::class.java).putExtra(SearchActivity.EXTRA_NUMBER, link.query)
                is Offers -> Intent(ctx, OffersActivity::class.java)
                    .putExtra(OffersActivity.EXTRA_BRAND, link.brand).putExtra(OffersActivity.EXTRA_NUMBER, link.number)
                is Order -> if (loggedIn) Intent(ctx, OrderDetailsActivity::class.java)
                    .putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, link.number) else null
                Chat -> Intent(ctx, ChatActivity::class.java)
                is News -> Intent(ctx, NewsActivity::class.java)
                    .putExtra(NewsActivity.EXTRA_URL, "${com.example.myapplication.News.BASE}news${link.page}")
                is Vin -> Intent(ctx, VinSearchActivity::class.java).apply { if (link.query.isNotBlank()) putExtra("prefillVin", link.query) }
                is Web -> Intent(Intent.ACTION_VIEW, Uri.parse(link.url))
            }
            Analytics.event("deeplink_open", mapOf("kind" to (link?.javaClass?.simpleName ?: "none"), "guest" to !loggedIn))
            when {
                target == null -> activity.startActivity(if (loggedIn) home else login)
                link is Web -> {
                    if (withHome) activity.startActivity(home)
                    com.example.myapplication.ui.openExternal(activity, target)
                }
                withHome -> activity.startActivities(arrayOf(home, target))
                else -> activity.startActivity(target)
            }
        }
    }
}

/** Единая точка входа для ссылок avtodrug92:// и нажатий на уведомления со ссылкой. Без своего экрана. */
class DeepLinkActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        DeepLink.open(this, DeepLink.parse(DeepLink.from(intent)), withHome = isTaskRoot)
        finish()
    }
}
