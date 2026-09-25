package com.example.myapplication.abcp

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.ui.theme.AvtodrugTheme
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Лента уведомлений: все изменения статусов, новые сверху. */
@OptIn(ExperimentalMaterial3Api::class)
class NotificationsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val feed = OrderStatusWatch.feed(this)
        OrderStatusWatch.markSeen(this)
        val fmt = SimpleDateFormat("d MMM, HH:mm", Locale("ru"))

        setContent {
            AvtodrugTheme {
                Scaffold(topBar = { TopAppBar(title = { Text("Уведомления") }, actions = { com.example.myapplication.ui.SearchAction() }) }) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        if (feed.isEmpty()) {
                            Text(
                                "Пока уведомлений нет. Здесь появятся изменения статусов ваших заказов.",
                                Modifier.align(Alignment.Center).padding(24.dp)
                            )
                        } else LazyColumn(
                            contentPadding = PaddingValues(12.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            items(feed) { f ->
                                ElevatedCard(Modifier.fillMaxWidth().clickable {
                                    startActivity(
                                        Intent(this@NotificationsActivity, OrderDetailsActivity::class.java)
                                            .putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, f.order)
                                    )
                                }) {
                                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Text("Заказ № ${f.order}", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                                            Text(fmt.format(Date(f.time)), style = MaterialTheme.typography.bodySmall)
                                        }
                                        Text(f.title)
                                        StatusChip(f.status, null)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
