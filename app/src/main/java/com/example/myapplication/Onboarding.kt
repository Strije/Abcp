package com.example.myapplication

import android.Manifest
import android.content.Context
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.launch

/** Обучение: приветствие при первом запуске и разовые подсказки на экранах. */
object Tips {
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("tips", Context.MODE_PRIVATE)
    fun onboardingDone(ctx: Context) = prefs(ctx).getBoolean("onboarding", false)
    fun setOnboardingDone(ctx: Context) = prefs(ctx).edit().putBoolean("onboarding", true).apply()
    fun seen(ctx: Context, key: String) = prefs(ctx).getBoolean("hint_$key", false)
    fun markSeen(ctx: Context, key: String) = prefs(ctx).edit().putBoolean("hint_$key", true).apply()
}

private data class Slide(val emoji: String, val title: String, val text: String)

private val SLIDES = listOf(
    Slide("🔎", "Ищите как удобно", "Артикул, VIN, номер кузова или госномер — в одном поле на главной. Приложение само поймёт, что вы ввели."),
    Slide("📷", "Фото вместо ввода", "Сфотографируйте табличку VIN, СТС или упаковку детали — номер распознается прямо на телефоне."),
    Slide("🚗", "Моя машина", "Добавьте машину в гараж и отметьте ★ — на главной появятся фильтры, тормоза и другие разделы в одно касание."),
    Slide("🔔", "Заказы под контролем", "Сообщим, когда заказ готов к выдаче, и покажем маршрут до магазина. Оплатить долг или пополнить баланс — прямо в приложении.")
)

/** Приветствие: 4 экрана с листанием. На последнем — зачем уведомления, и только потом запрос Android. */
@Composable
fun OnboardingScreen(onFinish: () -> Unit) {
    val ctx = LocalContext.current
    val pager = rememberPagerState { SLIDES.size }
    val scope = rememberCoroutineScope()
    val last = pager.currentPage == SLIDES.lastIndex
    fun finish() { Tips.setOnboardingDone(ctx); Analytics.event("onboarding_done", mapOf("page" to pager.currentPage + 1)); onFinish() }
    val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { finish() }

    Column(Modifier.fillMaxSize().systemBarsPadding().padding(24.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            if (!last) TextButton(onClick = { finish() }) { Text("Пропустить") }
        }
        HorizontalPager(state = pager, modifier = Modifier.weight(1f)) { i ->
            val s = SLIDES[i]
            Column(
                Modifier.fillMaxSize(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Box(
                    Modifier.size(140.dp).background(MaterialTheme.colorScheme.primaryContainer, CircleShape),
                    contentAlignment = Alignment.Center
                ) { Text(s.emoji, fontSize = 64.sp) }
                Spacer(Modifier.height(32.dp))
                Text(s.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                Spacer(Modifier.height(12.dp))
                Text(s.text, style = MaterialTheme.typography.bodyLarge, textAlign = TextAlign.Center,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        // Точки — где мы
        Row(Modifier.fillMaxWidth().padding(vertical = 16.dp), horizontalArrangement = Arrangement.Center) {
            SLIDES.indices.forEach { i ->
                Box(
                    Modifier.padding(4.dp).size(if (i == pager.currentPage) 10.dp else 8.dp)
                        .background(
                            if (i == pager.currentPage) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant,
                            CircleShape
                        )
                )
            }
        }
        Button(
            onClick = {
                when {
                    !last -> scope.launch { pager.animateScrollToPage(pager.currentPage + 1) }
                    Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                        ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED ->
                        askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
                    else -> finish()
                }
            },
            modifier = Modifier.fillMaxWidth().height(52.dp)
        ) { Text(if (last) "Включить уведомления и начать" else "Далее") }
        if (last) TextButton(onClick = { finish() }, modifier = Modifier.fillMaxWidth()) { Text("Начать без уведомлений") }
    }
}

/** «Как пользоваться» из профиля — то же приветствие ещё раз. */
class OnboardingActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { Surface { OnboardingScreen(onFinish = { finish() }) } } }
    }
}

/** Разовая подсказка на экране: показывается, пока не нажали «Понятно». */
@Composable
fun OneTimeHint(key: String, text: String, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    var visible by remember { mutableStateOf(!Tips.seen(ctx, key)) }
    if (!visible) return
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
        shape = RoundedCornerShape(14.dp),
        modifier = modifier.fillMaxWidth()
    ) {
        Row(Modifier.padding(start = 14.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("💡", modifier = Modifier.padding(end = 10.dp))
            Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = { Tips.markSeen(ctx, key); visible = false }) { Text("Понятно") }
        }
    }
}
