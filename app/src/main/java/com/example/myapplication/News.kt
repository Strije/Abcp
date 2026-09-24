package com.example.myapplication

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Новости и акции на главной — страницы сайта avtodrug92.ru/news1…news4.
 * На странице в ABCP кладётся HTML-фрагмент (картинка, заголовок, текст) в режиме «без шаблона»:
 * тогда страница отдаёт только его, и приложение показывает его карточкой.
 * Страницы, которые отдают сайт целиком (пустые/с шаблоном), пропускаются.
 */
object News {
    const val BASE = "https://avtodrug92.ru/"
    val pages = (1..4).map { "${BASE}news$it" }

    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()

    data class Item(val url: String, val html: String)

    suspend fun load(): List<Item> = coroutineScope {
        pages.map { url ->
            async(Dispatchers.IO) {
                runCatching {
                    http.newCall(Request.Builder().url(url).build()).execute().use { r ->
                        val body = r.body?.string().orEmpty().trim()
                        // Страница целиком с оформлением сайта — значит, новость туда не положена
                        val isWholeSite = body.contains("<body class=", ignoreCase = true) && body.length > 20_000
                        if (r.isSuccessful && body.isNotBlank() && !isWholeSite) Item(url, body) else null
                    }
                }.getOrNull()
            }
        }.awaitAll().filterNotNull()
    }

    /** Фрагмент со страницы → полноценный документ под ширину телефона. */
    fun wrap(fragment: String, dark: Boolean): String {
        val fg = if (dark) "#E8EAF0" else "#1B1F27"
        return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 html,body{margin:0;padding:0;background:transparent;color:$fg;font-family:sans-serif;font-size:15px;line-height:1.45}
 body{padding:12px;box-sizing:border-box}
 img{max-width:100%;height:auto;border-radius:10px;display:block}
 h1,h2,h3{margin:.3em 0;line-height:1.2}
 a{color:#F08A24}
</style></head><body>$fragment</body></html>"""
    }
}

@SuppressLint("SetJavaScriptEnabled")
private fun newsWebView(ctx: Context, html: String, dark: Boolean, interactive: Boolean): WebView =
    WebView(ctx).apply {
        setBackgroundColor(0)
        settings.javaScriptEnabled = false
        isVerticalScrollBarEnabled = interactive
        webViewClient = object : WebViewClient() {
            // Ссылки из новости открываем в браузере, а не внутри карточки
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                ctx.startActivity(Intent(Intent.ACTION_VIEW, request.url))
                return true
            }
        }
        loadDataWithBaseURL(News.BASE, News.wrap(html, dark), "text/html", "utf-8", null)
    }

/** Карусель новостей на главной. Пока новостей нет — блок не показывается. */
@Composable
fun NewsCarousel() {
    val ctx = LocalContext.current
    var items by remember { mutableStateOf<List<News.Item>>(emptyList()) }
    LaunchedEffect(Unit) { items = runCatching { News.load() }.getOrDefault(emptyList()) }
    if (items.isEmpty()) return
    val dark = androidx.compose.foundation.isSystemInDarkTheme()

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Новости и акции", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            items.forEach { item ->
                Card(
                    Modifier.width(if (items.size == 1) 340.dp else 280.dp).height(190.dp),
                    shape = MaterialTheme.shapes.large
                ) {
                    Box(Modifier.fillMaxSize()) {
                        AndroidView(
                            factory = { newsWebView(it, item.html, dark, interactive = false) },
                            modifier = Modifier.fillMaxSize()
                        )
                        // Прозрачный слой сверху: нажатие открывает новость целиком
                        Box(Modifier.matchParentSize().clickable {
                            ctx.startActivity(
                                Intent(ctx, NewsActivity::class.java)
                                    .putExtra(NewsActivity.EXTRA_URL, item.url)
                                    .putExtra(NewsActivity.EXTRA_HTML, item.html)
                            )
                        })
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
class NewsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val html = intent.getStringExtra(EXTRA_HTML).orEmpty()
        val url = intent.getStringExtra(EXTRA_URL).orEmpty()
        setContent {
            AvtodrugTheme {
                val dark = androidx.compose.foundation.isSystemInDarkTheme()
                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = { Text("Новости и акции") },
                            actions = {
                                if (url.isNotBlank()) TextButton(onClick = {
                                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                                }) { Text("На сайте") }
                            }
                        )
                    }
                ) { padding ->
                    AndroidView(
                        factory = { newsWebView(it, html, dark, interactive = true) },
                        modifier = Modifier.padding(padding).fillMaxSize()
                    )
                }
            }
        }
    }

    companion object {
        const val EXTRA_URL = "extra_url"
        const val EXTRA_HTML = "extra_html"
    }
}
