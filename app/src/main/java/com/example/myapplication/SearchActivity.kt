package com.example.myapplication
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.AbcpShop
import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.CartIconButton
import com.example.myapplication.abcp.OffersActivity
import com.example.myapplication.abcp.autoPickBrand
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Поиск по артикулу: номер → список брендов → карточка с ценами. */
@OptIn(ExperimentalMaterial3Api::class)
class SearchActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefillNumber = intent.getStringExtra(EXTRA_NUMBER).orEmpty()
        val preferredBrand = intent.getStringExtra(EXTRA_BRAND)
        setContent { AvtodrugTheme { SearchScreen(prefillNumber, preferredBrand) } }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(prefillNumber: String = "", preferredBrand: String? = null) {
    val ctx = LocalContext.current
    val shop = remember { AbcpShop(SessionManager(ctx)) }
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf(prefillNumber) }
    var tips by remember { mutableStateOf<List<BrandHit>>(emptyList()) }
    var brands by remember { mutableStateOf<List<BrandHit>?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun search() {
        val n = query.trim()
        if (n.isBlank()) return
        tips = emptyList()
        loading = true
        error = null
        scope.launch {
            try {
                // Бренды в наличии — наверху списка
                val found = shop.brands(n).sortedByDescending { it.available }
                brands = found
                // Выбор бренда пропускаем, когда он однозначен (один, известен заранее, единственный в наличии)
                autoPickBrand(found, preferredBrand)?.let { openOffers(ctx, it) }
            } catch (e: Exception) {
                error = e.message ?: "Ошибка поиска"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { if (prefillNumber.isNotBlank()) search() }

    // Подсказки по мере ввода, с паузой, чтобы не дёргать сервер на каждую букву
    LaunchedEffect(query) {
        val q = query.trim()
        if (q.length < 3) {
            tips = emptyList(); return@LaunchedEffect
        }
        delay(400)
        tips = runCatching { shop.tips(q) }.getOrDefault(emptyList()).take(8)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Поиск по артикулу") },
                actions = { CartIconButton() }
            )
        }
    ) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it; brands = null },
                label = { Text("Артикул или OEM-номер") },
                singleLine = true,
                trailingIcon = {
                    IconButton(onClick = { search() }) { Icon(Icons.Default.Search, "Найти") }
                },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { search() }),
                modifier = Modifier.fillMaxWidth()
            )

            when {
                loading -> Box(Modifier.fillMaxWidth().padding(24.dp)) {
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                }
                error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                brands != null && brands!!.isEmpty() ->
                    Text("По номеру «${query.trim()}» ничего не найдено")
                brands != null -> {
                    Text("Выберите производителя", style = MaterialTheme.typography.titleSmall)
                    HitList(brands!!) { openOffers(ctx, it) }
                }
                tips.isNotEmpty() -> HitList(tips) { openOffers(ctx, it) }
            }
        }
    }
}

private fun openOffers(ctx: Context, hit: BrandHit) {
    ctx.startActivity(
        Intent(ctx, OffersActivity::class.java)
            .putExtra(OffersActivity.EXTRA_BRAND, hit.brand)
            .putExtra(OffersActivity.EXTRA_NUMBER, hit.number)
            .putExtra(OffersActivity.EXTRA_DESCRIPTION, hit.description)
    )
}

@Composable
private fun HitList(hits: List<BrandHit>, onClick: (BrandHit) -> Unit) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items(hits) { h ->
            ElevatedCard(Modifier.fillMaxWidth().clickable { onClick(h) }) {
                Column(Modifier.padding(12.dp)) {
                    Text(
                        "${h.brand}  ${h.number}" + if (h.available) "  · в наличии" else "",
                        style = MaterialTheme.typography.titleSmall
                    )
                    if (h.description.isNotBlank()) {
                        Text(h.description, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}
