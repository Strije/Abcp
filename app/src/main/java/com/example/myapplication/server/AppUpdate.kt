package com.example.myapplication.server

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.example.myapplication.BuildConfig
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class Release(
    val versionCode: Int,
    val versionName: String,
    val notes: String,
    val size: Long,
    val sha256: String,
    val url: String
)

/**
 * Автообновление до публикации в магазине: CI кладёт каждую сборку main на наш сервер,
 * приложение при запуске спрашивает /v1/app/latest и предлагает поставить новую.
 * Поверх ставится только APK с той же подписью — это проверяет сам Android.
 */
class AppUpdate(private val ctx: Context) {

    private val prefs = ctx.getSharedPreferences("app_update", Context.MODE_PRIVATE)
    private val base = BuildConfig.SERVER_URL.trimEnd('/')

    /** Новая сборка или null. Без [force] — не чаще раза в 6 часов и не после «Позже» на сутки. */
    suspend fun check(force: Boolean): Release? = withContext(Dispatchers.IO) {
        if (base.isBlank()) return@withContext null
        val now = System.currentTimeMillis()
        if (!force && now - prefs.getLong(LAST_CHECK, 0) < 6 * HOUR) return@withContext null
        val text = http.newCall(Request.Builder().url("$base/v1/app/latest").build()).execute().use {
            if (it.code == 404) return@withContext null
            if (!it.isSuccessful) throw ServerException("Сервер обновлений недоступен (${it.code})", it.code)
            it.body?.string().orEmpty()
        }
        prefs.edit().putLong(LAST_CHECK, now).apply()
        val o = JsonParser.parseString(text).asJsonObject
        val r = Release(
            versionCode = o["versionCode"].asInt,
            versionName = o["versionName"]?.asString.orEmpty(),
            notes = o["notes"]?.asString.orEmpty(),
            size = o["size"]?.asLong ?: 0,
            sha256 = o["sha256"].asString,
            url = base + o["url"].asString
        )
        when {
            r.versionCode <= BuildConfig.VERSION_CODE -> null
            !force && prefs.getInt(SNOOZE_CODE, 0) == r.versionCode && now < prefs.getLong(SNOOZE_UNTIL, 0) -> null
            else -> r
        }
    }

    fun snooze(r: Release) {
        prefs.edit().putInt(SNOOZE_CODE, r.versionCode)
            .putLong(SNOOZE_UNTIL, System.currentTimeMillis() + 24 * HOUR).apply()
    }

    /** Скачивает APK в кэш и сверяет контрольную сумму — битый файл установщик не получит. */
    suspend fun download(r: Release, onProgress: (Float) -> Unit): File = withContext(Dispatchers.IO) {
        val dir = File(ctx.cacheDir, "update").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() } // прошлые сборки больше не нужны
        val file = File(dir, "avtodrug92-${r.versionCode}.apk")
        val md = MessageDigest.getInstance("SHA-256")
        http.newCall(Request.Builder().url(r.url).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw ServerException("Не удалось скачать обновление (${resp.code})", resp.code)
            val body = resp.body ?: throw ServerException("Пустой ответ сервера")
            val total = body.contentLength().takeIf { it > 0 } ?: r.size
            body.byteStream().use { input ->
                file.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        md.update(buf, 0, n)
                        done += n
                        if (total > 0) onProgress(done.toFloat() / total)
                    }
                }
            }
        }
        val sum = md.digest().joinToString("") { "%02x".format(it) }
        if (!sum.equals(r.sha256, ignoreCase = true)) {
            file.delete()
            throw ServerException("Файл обновления повреждён, попробуйте ещё раз")
        }
        file
    }

    /** Android 8+: ставить APK можно только с разрешения «Установка неизвестных приложений». */
    fun canInstall(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O || ctx.packageManager.canRequestPackageInstalls()

    fun openInstallPermission() {
        ctx.startActivity(
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    fun install(file: File) {
        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", file)
        ctx.startActivity(
            Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    companion object {
        private const val HOUR = 3600_000L
        private const val LAST_CHECK = "last_check"
        private const val SNOOZE_CODE = "snooze_code"
        private const val SNOOZE_UNTIL = "snooze_until"
        private val http = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}

/** Окно «Доступно обновление»: скачать с прогрессом → разрешение на установку → установщик Android. */
@Composable
fun UpdateDialog(release: Release, onLater: () -> Unit) {
    val ctx = LocalContext.current
    val updater = remember { AppUpdate(ctx) }
    val scope = rememberCoroutineScope()
    var progress by remember { mutableStateOf<Float?>(null) }
    var file by remember { mutableStateOf<File?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val mb = if (release.size > 0) " · %.1f МБ".format(release.size / 1048576.0) else ""

    fun installOrAsk(f: File) {
        if (updater.canInstall()) updater.install(f) else updater.openInstallPermission()
    }

    AlertDialog(
        onDismissRequest = { if (progress == null) onLater() },
        title = { Text("Доступно обновление") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Версия ${release.versionName}$mb", style = MaterialTheme.typography.bodyMedium)
                if (release.notes.isNotBlank()) Text(release.notes, style = MaterialTheme.typography.bodySmall)
                when {
                    file != null && !updater.canInstall() -> Text(
                        "Разрешите приложению установку обновлений в открывшихся настройках, затем нажмите «Установить».",
                        style = MaterialTheme.typography.bodySmall
                    )
                    progress != null && file == null -> LinearProgressIndicator(
                        progress = { progress ?: 0f }, modifier = Modifier.fillMaxWidth()
                    )
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            val f = file
            Button(
                enabled = progress == null || f != null,
                onClick = {
                    if (f != null) { installOrAsk(f); return@Button }
                    error = null
                    progress = 0f
                    scope.launch {
                        try {
                            val downloaded = updater.download(release) { p -> progress = p }
                            file = downloaded
                            installOrAsk(downloaded)
                        } catch (e: Exception) {
                            error = e.message ?: "Не удалось скачать"
                            progress = null
                        }
                    }
                }
            ) { Text(if (f != null) "Установить" else if (progress != null) "Скачиваем…" else "Обновить") }
        },
        dismissButton = {
            if (progress == null || file != null) TextButton(onClick = onLater) { Text("Позже") }
        }
    )
}
