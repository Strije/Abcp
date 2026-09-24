package com.example.myapplication.laximo
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.myapplication.SearchActivity
import com.example.myapplication.laximo.model.LaximoDetail
import com.example.myapplication.laximo.model.LaximoImageMapItem
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class UnitDetailsActivity : ComponentActivity() {

    private val repo = LaximoRepository()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog = intent.getStringExtra("catalog") ?: ""
        val vehicleId = intent.getStringExtra("vehicleId") ?: "0"
        val unitId = intent.getStringExtra("unitId") ?: ""
        val unitName = intent.getStringExtra("unitName") ?: ""
        val unitSsd = intent.getStringExtra("unitSsd") ?: ""
        val imageUrl = intent.getStringExtra("imageUrl")

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = unitSsd)
        val unit = LaximoUnit(unitId = unitId, name = unitName, ssd = unitSsd, imageUrl = imageUrl)

        setContent {
            AvtodrugTheme {
                UnitDetailsScreen(ctx = ctx, unit = unit, repo = repo)
            }
        }
    }
}


@Composable
private fun UnitDetailsScreen(
    ctx: LaximoVehicleContext,
    unit: LaximoUnit,
    repo: LaximoRepository
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var details by remember { mutableStateOf<List<LaximoDetail>>(emptyList()) }
    var mapItems by remember { mutableStateOf<List<LaximoImageMapItem>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var selectedCode by remember { mutableStateOf<String?>(null) }
    var isInteractingWithScheme by remember { mutableStateOf(false) }

    LaunchedEffect(ctx.catalog, unit.unitId, unit.ssd) {
        scope.launch {
            runCatching {
                details = repo.listDetailByUnit(ctx, unit)
                mapItems = repo.listImageMapByUnit(ctx, unit)
            }.onFailure { e ->
                error = e.message
            }
        }
    }

    val codes = remember(details) {
        details.mapNotNull { it.codeOnImage?.trim() }
            .filter { it.isNotBlank() }
            .distinct()
    }

    fun onDetailClick(detail: LaximoDetail) {
        detail.codeOnImage?.let { code ->
            if (code.isNotBlank()) {
                selectedCode = code
            }
        }
    }

    fun onCartClick(oem: String) {
        // OEM со схемы → поиск ABCP: бренды → цены и аналоги
        context.startActivity(
            Intent(context, SearchActivity::class.java).apply {
                putExtra(SearchActivity.EXTRA_BRAND, "")
                putExtra(SearchActivity.EXTRA_NUMBER, oem)
            }
        )
    }

    @OptIn(ExperimentalMaterial3Api::class)
    Scaffold(
        topBar = { TopAppBar(title = { Text(unit.name, maxLines = 2) }) }
    ) { pad ->
        LazyColumn(
            modifier = Modifier
                .padding(pad)
                .fillMaxSize(),
            userScrollEnabled = !isInteractingWithScheme
        ) {

            item {
                val img = resolveLaximoImageUrl(unit.largeImageUrl ?: unit.imageUrl, LaximoImageSize.SOURCE)
                if (img != null) {
                    Card(Modifier.padding(12.dp).fillMaxWidth()) {
                        UnitSchemeWithMap(
                            imageUrl = img,
                            mapItems = mapItems,
                            selectedCode = selectedCode,
                            showNumbers = true,
                            onSelectCode = { code ->
                                selectedCode = code
                            },
                            onInteractingChange = { isInteractingWithScheme = it }
                        )
                    }
                }
            }

            if (codes.isNotEmpty()) {
                item {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Номера на схеме",
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(horizontal = 12.dp)
                    )
                    LazyRow(
                        modifier = Modifier.fillMaxWidth(),
                        contentPadding = PaddingValues(horizontal = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        items(codes) { code ->
                            val isSel = selectedCode == code
                            AssistChip(
                                onClick = { selectedCode = code },
                                label = { Text(code) }
                            )
                        }
                    }
                }
            }

            items(details) { d ->
                DetailCard(
                    d = d,
                    selected = !selectedCode.isNullOrBlank() && d.codeOnImage == selectedCode,
                    onClick = { onDetailClick(d) },
                    onCartClick = { onCartClick(it) }
                )
            }

            if (error != null) {
                item {
                    Text(
                        "Ошибка: $error",
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(12.dp)
                    )
                }
            }
        }
    }
}
