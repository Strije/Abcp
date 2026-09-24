package com.example.myapplication

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.example.myapplication.ui.theme.AvtodrugTheme

/**
 * Чат с менеджером внутри приложения: онлайн-чат открытой линии Битрикс24 (тот же, что на сайте).
 * Сообщения попадают в CRM. Куки сохраняются — клиент видит свою переписку при следующем входе.
 */
@OptIn(ExperimentalMaterial3Api::class)
class ChatActivity : ComponentActivity() {

    private var fileCallback: ValueCallback<Array<Uri>>? = null

    // Прикрепить фото детали / СТС из галереи или файлов
    private val pickFiles = registerForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        fileCallback?.onReceiveValue(uris.toTypedArray())
        fileCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            CookieManager.getInstance().setAcceptCookie(true)
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
            webViewClient = WebViewClient() // ссылки чата открываются здесь же
            webChromeClient = object : WebChromeClient() {
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
                Scaffold(topBar = { TopAppBar(title = { Text("Чат с менеджером") }) }) { padding ->
                    AndroidView(factory = { web }, modifier = Modifier.padding(padding).fillMaxSize())
                }
            }
        }
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush() // не потерять сессию чата
    }
}
