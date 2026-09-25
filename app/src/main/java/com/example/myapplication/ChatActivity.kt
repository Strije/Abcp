package com.example.myapplication

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.example.myapplication.ui.ErrorState
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.google.gson.Gson
import org.json.JSONArray
import org.json.JSONObject

/**
 * Чат с менеджером: онлайн-чат открытой линии Битрикс24 (тот же, что на сайте, сообщения идут в CRM).
 * Страницу Битрикса приводим к виду приложения (без их шапки и обоев), а вошедшему клиенту анкету
 * «Как к Вам обращаться» не показываем — имя и телефон отдаём виджету через его API
 * (setUserRegisterData), оператору — откуда клиент и его ID в ABCP (setCustomData).
 */
@OptIn(ExperimentalMaterial3Api::class)
class ChatActivity : ComponentActivity() {

    private var fileCallback: ValueCallback<Array<Uri>>? = null

    // Прикрепить фото детали / СТС из галереи или файлов
    private val pickFiles = registerForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        fileCallback?.onReceiveValue(uris.toTypedArray())
        fileCallback = null
    }

    private var loading by mutableStateOf(true)
    private var progress by mutableFloatStateOf(0f)
    private var failed by mutableStateOf(false)

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val chatHost = Uri.parse(StoreInfo.managerChatUrl).host

        val web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
            // До загрузки страницы: наши стили и данные клиента для виджета
            if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                WebViewCompat.addDocumentStartJavaScript(this, startScript(), setOf("https://$chatHost"))
            }
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    // Ссылки из переписки (сайт, товары, карты) — во внешнем браузере, чат остаётся на месте
                    if (request.url.host == chatHost) return false
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url)) }
                    return true
                }

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    loading = true
                    failed = false
                }

                override fun onPageFinished(view: WebView, url: String?) {
                    loading = false
                    // Старые WebView без DOCUMENT_START_SCRIPT — хотя бы стили
                    if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                        view.evaluateJavascript(STYLE_JS, null)
                    }
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) { failed = true; loading = false }
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onProgressChanged(view: WebView?, newProgress: Int) {
                    progress = newProgress / 100f
                }

                override fun onShowFileChooser(
                    view: WebView?,
                    callback: ValueCallback<Array<Uri>>?,
                    params: FileChooserParams?
                ): Boolean {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = callback
                    pickFiles.launch("*/*")
                    return true
                }
            }
            loadUrl(StoreInfo.managerChatUrl)
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        setContent {
            AvtodrugTheme {
                var callMenu by remember { mutableStateOf(false) }
                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Column {
                                    Text("Чат с менеджером")
                                    Text(StoreInfo.hours, style = MaterialTheme.typography.bodySmall)
                                }
                            },
                            actions = {
                                TextButton(onClick = {
                                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(StoreInfo.maxChatUrl))) }
                                }) { Text("MAX") }
                                Box {
                                    IconButton(onClick = { callMenu = true }) { Icon(Icons.Default.Call, "Позвонить") }
                                    DropdownMenu(expanded = callMenu, onDismissRequest = { callMenu = false }) {
                                        StoreInfo.stores.forEach { s ->
                                            DropdownMenuItem(
                                                text = { Column { Text(s.phoneDisplay); Text(s.address, style = MaterialTheme.typography.bodySmall) } },
                                                onClick = {
                                                    callMenu = false
                                                    startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${s.phone}")))
                                                }
                                            )
                                        }
                                    }
                                }
                            }
                        )
                    }
                ) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        AndroidView(factory = { web }, modifier = Modifier.fillMaxSize())
                        if (loading && !failed) LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth())
                        if (failed) Surface(Modifier.fillMaxSize()) {
                            Box(contentAlignment = androidx.compose.ui.Alignment.Center) {
                                ErrorState(
                                    "Чат не открылся — нет связи с интернетом.\nМожно позвонить в магазин или написать в MAX.",
                                    onRetry = { failed = false; web.reload() }
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    /** Скрипт до загрузки страницы: стили + данные вошедшего клиента для виджета Битрикс24. */
    private fun startScript(): String {
        val session = SessionManager(this)
        val user = session.userJson()?.let { runCatching { Gson().fromJson(it, UserInfoDto::class.java) }.getOrNull() }
        val sb = StringBuilder(STYLE_JS)
        if (session.isLoggedIn() && user != null) {
            val register = JSONObject().apply {
                // Виджет принимает только name, lastName, email, www, gender, position — телефона тут нет
                user.name?.takeIf { it.isNotBlank() }?.let { put("name", it) }
                user.email?.takeIf { it.isNotBlank() }?.let { put("email", it) }
            }
            val custom = JSONArray().apply {
                put(JSONObject().put("USER", JSONObject().put("NAME", user.name.orEmpty())))
                put(JSONObject().put("GRID", JSONArray().apply {
                    put(line("Откуда", "Приложение «Автодруг» ${BuildConfig.VERSION_NAME}, Android"))
                    user.id?.let { put(line("ID клиента в ABCP", it.toString())) }
                    user.mobile?.takeIf { it.isNotBlank() }?.let { put(line("Телефон", it)) }
                }))
            }
            // Приветственная CRM-форма «Как к Вам обращаться» (имя + телефон) включена в настройках линии —
            // заполняем её из профиля, отправляет клиент сам (там же его согласие на обработку данных)
            val prefill = JSONObject().put("name", user.name.orEmpty()).put("phone", user.mobile.orEmpty())
            sb.append(
                """
                (function () {
                  var data = $prefill, done = {};
                  function fill(el, v) {
                    if (!v || done[el.name] || el.value) return;
                    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    // фокус/уход с поля — форма поднимает подпись над значением
                    el.dispatchEvent(new FocusEvent('focus')); el.dispatchEvent(new FocusEvent('blur'));
                    done[el.name] = true;
                  }
                  new MutationObserver(function () {
                    var n = document.querySelector('.b24-form input[name="name"]');
                    var p = document.querySelector('.b24-form input[name="phone"]');
                    if (n) fill(n, data.name);
                    if (p) fill(p, data.phone.length === 11 ? '+' + data.phone : data.phone);
                  }).observe(document.documentElement, { childList: true, subtree: true });
                })();
                """.trimIndent()
            )
            // Документированный способ: событие onBitrixLiveChat при создании виджета
            sb.append(
                """
                window.addEventListener('onBitrixLiveChat', function (e) {
                  try {
                    var w = e.detail.widget;
                    w.setUserRegisterData($register);
                    w.setCustomData($custom);
                  } catch (err) {}
                });
                """.trimIndent()
            )
        }
        return sb.toString()
    }

    private fun line(name: String, value: String) =
        JSONObject().put("NAME", name).put("VALUE", value).put("DISPLAY", "LINE")

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush() // не потерять сессию чата
    }

    companion object {
        /** Страница Битрикса в стиле приложения: без их шапки (есть наша), без обоев, фон как в приложении. */
        private val STYLE_JS = """
            (function () {
              var css = '.bx-livechat-head-wrap{display:none!important}' +
                '.bx-livechat-body{background:#F3F5F9!important;background-image:none!important}' +
                'body,.bx-livechat-wrapper,.bx-livechat-box{background:#F3F5F9!important}' +
                '.bottom-cloud,.left-cloud,.right-cloud{display:none!important}';
              function add() {
                if (document.getElementById('avtodrug-style')) return;
                var s = document.createElement('style'); s.id = 'avtodrug-style'; s.textContent = css;
                (document.head || document.documentElement).appendChild(s);
              }
              if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add);
              document.addEventListener('DOMContentLoaded', add);
            })();
        """.trimIndent()
    }
}
