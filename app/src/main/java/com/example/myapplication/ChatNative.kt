package com.example.myapplication

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.server.AppServer
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class ChatMessage(val id: Long, val dir: String, val text: String, val author: String, val ts: Long, val failed: Boolean = false)

/**
 * Свой чат с менеджером: сообщения идут через наш сервер в открытую линию Битрикс24,
 * ответы операторов приходят push-уведомлением и появляются здесь.
 */
@Composable
fun NativeChat(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val server = remember { AppServer(ctx) }
    val scope = rememberCoroutineScope()
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    var input by rememberSaveableText()
    var sending by remember { mutableStateOf(false) }
    val list = rememberLazyListState()

    suspend fun refresh() {
        val after = messages.filter { !it.failed && it.id > 0 }.maxOfOrNull { it.id } ?: 0
        runCatching { server.chatMessages(after) }.getOrNull()?.let { fresh ->
            if (fresh.isNotEmpty()) messages = (messages.filter { m -> fresh.none { it.id == m.id } } + fresh).sortedBy { it.ts }
        }
    }
    // Пока экран открыт — подтягиваем ответы раз в 5 секунд (push приходит, даже когда закрыт)
    LaunchedEffect(Unit) { while (true) { refresh(); delay(5000) } }
    LaunchedEffect(messages.size) { if (messages.isNotEmpty()) list.animateScrollToItem(messages.size) }

    fun send(text: String, retryOf: ChatMessage? = null) {
        if (text.isBlank() || sending) return
        sending = true
        if (retryOf != null) messages = messages - retryOf
        val local = ChatMessage(-System.currentTimeMillis(), "in", text, "", System.currentTimeMillis() / 1000)
        messages = messages + local
        scope.launch {
            val ok = runCatching { server.chatSend(text) }
            messages = messages - local + (if (ok.isSuccess) local.copy(id = ok.getOrThrow()) else local.copy(failed = true))
            if (ok.isSuccess) Analytics.event("chat_send") else ok.exceptionOrNull()?.let { Analytics.error("Чат → отправка", it) }
            sending = false
        }
    }

    Column(modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            item {
                Text(
                    "Напишите вопрос — ответит менеджер магазина. Можно прислать VIN, артикул или описать, что ищете.\n" +
                        "Отвечаем в рабочее время: ${StoreInfo.hours}.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 8.dp)
                )
            }
            items(messages, key = { it.id }) { m -> Bubble(m, onRetry = { send(m.text, m) }) }
        }
        Surface(tonalElevation = 3.dp) {
            Row(Modifier.fillMaxWidth().navigationBarsPadding().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = input, onValueChange = { input = it },
                    placeholder = { Text("Сообщение") }, maxLines = 5,
                    shape = RoundedCornerShape(20.dp), modifier = Modifier.weight(1f)
                )
                IconButton(enabled = input.isNotBlank() && !sending, onClick = { val t = input.trim(); input = ""; send(t) }) {
                    Icon(Icons.AutoMirrored.Filled.Send, "Отправить", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

@Composable
private fun rememberSaveableText() = androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf("") }

@Composable
private fun Bubble(m: ChatMessage, onRetry: () -> Unit) {
    val mine = m.dir == "in"
    val time = SimpleDateFormat("d MMM, HH:mm", Locale("ru")).format(Date(m.ts * 1000))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Column(
            Modifier.widthIn(max = 300.dp)
                .background(
                    if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                    RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = if (mine) 16.dp else 4.dp, bottomEnd = if (mine) 4.dp else 16.dp)
                )
                .then(if (m.failed) Modifier.clickable(onClick = onRetry) else Modifier)
                .padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            if (!mine && m.author.isNotBlank()) {
                Text(m.author, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            }
            Text(m.text, style = MaterialTheme.typography.bodyMedium)
            Text(
                if (m.failed) "Не отправлено — нажмите, чтобы повторить" else time,
                style = MaterialTheme.typography.labelSmall,
                color = if (m.failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.align(Alignment.End)
            )
        }
    }
}
