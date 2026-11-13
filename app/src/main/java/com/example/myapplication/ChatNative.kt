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
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.example.myapplication.server.AppServer
import com.example.myapplication.server.ServerException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** unsure — отправка оборвалась без ответа сервера: сообщение могло дойти. */
data class ChatMessage(
    val id: Long, val dir: String, val text: String, val author: String, val ts: Long,
    val failed: Boolean = false, val unsure: Boolean = false
)

/** Столько символов принимает сервер (ChatIn.text в app/main.py). */
internal const val CHAT_MAX_LEN = 2000
private const val CHAT_COUNTER_FROM = 200

/** Часы телефона и сервера могут расходиться — запас при сравнении времени, секунды. */
private const val CLOCK_SLACK_SEC = 300L

/** Подтверждённые сервером — по id, свои ещё не подтверждённые (id < 0) — в конце по порядку отправки; id не повторяются. */
internal fun sortChat(list: List<ChatMessage>): List<ChatMessage> =
    list.distinctBy { it.id }.sortedWith(compareBy<ChatMessage>({ it.id < 0 }, { if (it.id < 0) -it.id else it.id }))

/** Серверное сообщение — то самое, что отправляли с телефона как local. */
private fun sameSent(local: ChatMessage, server: ChatMessage): Boolean =
    server.id > 0 && server.dir == "in" && server.text.trim() == local.text.trim() && server.ts >= local.ts - CLOCK_SLACK_SEC

/** Копия нашего сообщения, появившаяся на сервере после отправки (afterId — последний id, известный до неё). */
internal fun deliveredCopy(messages: List<ChatMessage>, local: ChatMessage, afterId: Long): ChatMessage? =
    messages.firstOrNull { it.id > afterId && sameSent(local, it) }

/** Новые сообщения с сервера поверх текущего списка. */
internal fun mergeChat(current: List<ChatMessage>, fresh: List<ChatMessage>): List<ChatMessage> {
    val ids = fresh.map { it.id }.toSet()
    var rest = current.filter { it.id !in ids }
    // «Не отправлено» без ответа сервера, а сообщение всё-таки дошло — красную копию убираем
    for (f in fresh) {
        val twin = rest.firstOrNull { it.id < 0 && it.failed && it.unsure && sameSent(it, f) }
        if (twin != null) rest = rest.filter { it.id != twin.id }
    }
    return sortChat(rest + fresh)
}

/** Сервер принял сообщение под номером id: в списке остаётся одно — без локальной копии и без дубля от опроса. */
internal fun confirmSent(current: List<ChatMessage>, local: ChatMessage, id: Long): List<ChatMessage> {
    val server = current.firstOrNull { it.id == id }
    return sortChat(
        current.filter { it.id != local.id && it.id != id } + (server ?: local.copy(id = id, failed = false, unsure = false))
    )
}

/** Обрезка ввода до лимита сервера (пару символов эмодзи не рвём). */
internal fun clipChatText(s: String): String {
    if (s.length <= CHAT_MAX_LEN) return s
    val cut = s.take(CHAT_MAX_LEN)
    return if (cut.last().isHighSurrogate()) cut.dropLast(1) else cut
}

/**
 * Свой чат с менеджером: сообщения идут через наш сервер в открытую линию Битрикс24,
 * ответы операторов приходят push-уведомлением и появляются здесь.
 */
@Composable
fun NativeChat(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val server = remember { AppServer(ctx) }
    val scope = rememberCoroutineScope()
    val owner = LocalLifecycleOwner.current
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    var input by rememberSaveableText()
    // Сообщение, которое сейчас отправляется, и последний id с сервера, известный до отправки
    var pending by remember { mutableStateOf<Pair<ChatMessage, Long>?>(null) }
    // Сервер сохранил сообщение, но до менеджера оно не дошло — такую копию не показываем
    var hidden by remember { mutableStateOf<Set<Long>>(emptySet()) }
    val list = rememberLazyListState()

    suspend fun refresh() {
        val after = messages.filter { it.id > 0 }.maxOfOrNull { it.id } ?: 0L
        runCatching { server.chatMessages(after) }.getOrNull()?.let { fresh ->
            if (fresh.isNotEmpty()) messages = mergeChat(messages, fresh)
        }
    }
    // Пока экран на виду — подтягиваем ответы раз в 5 секунд; свернули — опрос стоит, вернулись — сразу обновляем
    // (push приходит, даже когда закрыт)
    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) { while (true) { refresh(); delay(5000) } }
    }

    // Опрос мог принести отправляемое сообщение раньше ответа сервера — пока показываем только его
    val sendingNow = pending
    val shown = messages.filter { m ->
        m.id !in hidden &&
            !(sendingNow != null && m.id == sendingNow.first.id && deliveredCopy(messages, sendingNow.first, sendingNow.second) != null)
    }
    LaunchedEffect(shown.size) { if (shown.isNotEmpty()) list.animateScrollToItem(shown.size) }

    fun send(text: String, retryOf: ChatMessage? = null) {
        if (text.isBlank() || pending != null) return
        val known = messages.filter { it.id > 0 }.maxOfOrNull { it.id } ?: 0L
        val now = System.currentTimeMillis()
        val local = ChatMessage(-now, "in", text, "", now / 1000)
        messages = sortChat(messages.filter { it.id != retryOf?.id } + local)
        pending = local to known
        scope.launch {
            val ok = runCatching { server.chatSend(text) }
            val id = ok.getOrNull()
            if (id != null) {
                messages = confirmSent(messages, local, id)
                Analytics.event("chat_send")
            } else {
                val e = ok.exceptionOrNull()
                // Сервер ответил ошибкой — до менеджера точно не дошло; без ответа (связь, таймаут) — могло дойти
                val refused = e is ServerException && e.code != 0
                refresh()
                val copy = deliveredCopy(messages, local, known)
                if (!refused && copy != null) {
                    messages = messages.filter { it.id != local.id } // дошло — показываем серверное
                } else {
                    if (refused && copy != null) hidden = hidden + copy.id
                    messages = messages.map { if (it.id == local.id) it.copy(failed = true, unsure = !refused) else it }
                }
                if (e != null) Analytics.error("Чат → отправка", e)
            }
            pending = null
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
            items(shown, key = { it.id }) { m -> Bubble(m, onRetry = { send(m.text, m) }) }
        }
        Surface(tonalElevation = 3.dp) {
            // Отступ под панель навигации: если его уже учёл экран (consumeWindowInsets), здесь он нулевой
            Row(Modifier.fillMaxWidth().navigationBarsPadding().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                val left = CHAT_MAX_LEN - input.length
                val counter: (@Composable () -> Unit)? =
                    if (left < CHAT_COUNTER_FROM) { { Text("Осталось символов: $left") } } else null
                OutlinedTextField(
                    value = input, onValueChange = { input = clipChatText(it) },
                    placeholder = { Text("Сообщение") }, maxLines = 5, supportingText = counter,
                    shape = RoundedCornerShape(20.dp), modifier = Modifier.weight(1f)
                )
                IconButton(enabled = input.isNotBlank() && pending == null, onClick = { val t = input.trim(); input = ""; send(t) }) {
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
