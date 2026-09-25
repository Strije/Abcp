package com.example.myapplication.abcp

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.BuildConfig
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

data class WarrantyCondition(val term: String, val text: String, val url: String)

/** Избранный бренд магазина: срок гарантии и рейтинг (панель ABCP) + условия (avtodrug92.ru/garantija). */
data class BrandWarranty(val name: String, val warranty: String, val rating: Double, val conditions: List<WarrantyCondition>)

/** Список гарантий с нашего сервера — один раз за запуск; без сети выдача работает и без значков. */
object Warranties {
    @Volatile private var cache: Map<String, BrandWarranty>? = null
    var page: String = "https://avtodrug92.ru/garantija"
        private set

    fun key(brand: String) = brand.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' || it in 'А'..'Я' || it == 'Ё' }

    suspend fun load(): Map<String, BrandWarranty> = cache ?: withContext(Dispatchers.IO) {
        runCatching {
            val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()
            val text = http.newCall(Request.Builder().url(BuildConfig.SERVER_URL.trimEnd('/') + "/v1/brands/warranty").build())
                .execute().use { it.body?.string().orEmpty() }
            val root = JsonParser.parseString(text).asJsonObject
            root.get("page")?.asString?.takeIf { it.isNotBlank() }?.let { page = it }
            root.getAsJsonObject("brands").entrySet().associate { (k, v) ->
                val o = v.asJsonObject
                k to BrandWarranty(
                    name = o["name"].asString,
                    warranty = o["warranty"].asString,
                    rating = o["rating"].asDouble,
                    conditions = o.getAsJsonArray("conditions").map { c ->
                        val co = c.asJsonObject
                        WarrantyCondition(co["term"].asString, co["text"].asString, co["url"]?.asString.orEmpty())
                    }
                )
            }
        }.getOrDefault(emptyMap()).also { if (it.isNotEmpty()) cache = it }
    }
}

/** Рейтинг бренда кубками: закрашенные по рейтингу (округляем), остальные бледные. */
@Composable
fun RatingCups(rating: Double, size: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.labelMedium) {
    val full = Math.round(rating).toInt().coerceIn(0, 5)
    Row {
        repeat(5) { i -> Text("🏆", style = size, modifier = Modifier.alpha(if (i < full) 1f else 0.2f)) }
    }
}

/** Плашка в карточке артикула, как на сайте: оранжевая «Гарантия 2 года*» (с переносом) + кубки рейтинга. */
@Composable
fun WarrantyBadge(w: BrandWarranty, onClick: () -> Unit) {
    Row(
        Modifier.padding(vertical = 3.dp).clickable(onClick = onClick),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        Text(
            w.warranty + "*",
            style = MaterialTheme.typography.labelMedium, color = androidx.compose.ui.graphics.Color.White,
            modifier = Modifier.weight(1f, fill = false)
                .background(androidx.compose.ui.graphics.Color(0xFFF7941D), androidx.compose.foundation.shape.RoundedCornerShape(6.dp))
                .padding(horizontal = 8.dp, vertical = 3.dp)
        )
        RatingCups(w.rating)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WarrantySheet(w: BrandWarranty, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    fun open(url: String) = runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Text(w.name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text("🛡 ${w.warranty}", style = MaterialTheme.typography.titleMedium)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                RatingCups(w.rating, MaterialTheme.typography.titleMedium)
                Text("рейтинг магазина: ${"%.1f".format(w.rating)} из 5", style = MaterialTheme.typography.bodySmall)
            }
            w.conditions.forEach { c ->
                HorizontalDivider()
                Text(c.term, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                Text(c.text, style = MaterialTheme.typography.bodyMedium)
                if (c.url.isNotBlank()) TextButton(onClick = { open(c.url) }, contentPadding = PaddingValues(0.dp)) {
                    Text("Условия производителя ↗")
                }
            }
            HorizontalDivider()
            Text("Как воспользоваться гарантией*", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            Text(
                "Понадобятся:\n" +
                    "• акт выполненных работ на установку и снятие детали;\n" +
                    "• кассовый чек СТО — подтверждение, что работы сделаны на квалифицированном сервисе;\n" +
                    "• акт дефектовки (заключение по рекламации) с технически грамотным описанием неисправности;\n" +
                    "• фото детали и маркировки — будут плюсом.",
                style = MaterialTheme.typography.bodyMedium
            )
            Text(
                "Чек о покупке у нас не нужен — продажу найдём сами. Поможем оформить акты на установку, снятие и заключение.",
                style = MaterialTheme.typography.bodyMedium, color = com.example.myapplication.ui.theme.DeliveryColors.today
            )
            TextButton(onClick = { open(Warranties.page) }, contentPadding = PaddingValues(0.dp)) {
                Text("Гарантия и возврат на avtodrug92.ru ↗")
            }
        }
    }
}
