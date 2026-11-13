package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** Список чата: один элемент на id (иначе LazyColumn падает), свои неподтверждённые — в конце. */
class ChatMergeTest {

    private fun server(id: Long, text: String, dir: String = "in", ts: Long = 1000) = ChatMessage(id, dir, text, "", ts)
    private fun local(text: String, ts: Long = 1000, ms: Long = 1_000_000) = ChatMessage(-ms, "in", text, "", ts)

    @Test
    fun pollBeforeSendAnswerLeavesOneMessage() {
        val mine = local("Здравствуйте")
        // Опрос принёс сообщение с настоящим id раньше ответа на отправку
        val afterPoll = mergeChat(listOf(mine), listOf(server(7, "Здравствуйте")))
        val done = confirmSent(afterPoll, mine, 7)
        assertEquals(listOf(7L), done.map { it.id })
    }

    @Test
    fun sendAnswerBeforePollLeavesOneMessage() {
        val mine = local("Здравствуйте")
        val done = mergeChat(confirmSent(listOf(mine), mine, 7), listOf(server(7, "Здравствуйте")))
        assertEquals(listOf(7L), done.map { it.id })
    }

    @Test
    fun mergeKeepsIdsUnique() {
        val merged = mergeChat(listOf(server(1, "а"), server(2, "б")), listOf(server(2, "б"), server(3, "в"), server(3, "в")))
        assertEquals(listOf(1L, 2L, 3L), merged.map { it.id })
    }

    @Test
    fun confirmedByIdLocalLast() {
        // Часы телефона отстают: по времени своё сообщение оказалось бы в начале
        val mine = local("вопрос", ts = 10)
        val sorted = sortChat(listOf(mine, server(5, "ответ", "out", ts = 500), server(4, "раньше", ts = 900)))
        assertEquals(listOf(4L, 5L, mine.id), sorted.map { it.id })
    }

    @Test
    fun localKeepSendOrder() {
        val first = local("раз", ms = 1000)
        val second = local("два", ms = 2000)
        assertEquals(listOf("раз", "два"), sortChat(listOf(second, first)).map { it.text })
    }

    @Test
    fun deliveredCopyFoundAfterTimeout() {
        val mine = local("Нужен фильтр", ts = 1000)
        val list = listOf(server(3, "Нужен фильтр", ts = 500), server(8, "Нужен фильтр", ts = 1002), mine)
        // id 3 — такое же старое сообщение, отправленное раньше: не считается
        assertEquals(8L, deliveredCopy(list, mine, afterId = 3)?.id)
        assertNotNull(deliveredCopy(list, mine, afterId = 0))
    }

    @Test
    fun deliveredCopyIgnoresOtherTextAndManager() {
        val mine = local("Нужен фильтр", ts = 1000)
        assertNull(deliveredCopy(listOf(server(8, "Нужен фильтр", "out"), server(9, "Другое"), mine), mine, afterId = 0))
        // Без известного id — старое сообщение с тем же текстом отсекается по времени
        assertNull(deliveredCopy(listOf(server(8, "Нужен фильтр", ts = 100), mine), mine, afterId = 0))
    }

    @Test
    fun lateCopyRemovesUnsureFailed() {
        val lost = local("Нужен фильтр").copy(failed = true, unsure = true)
        assertEquals(listOf(8L), mergeChat(listOf(lost), listOf(server(8, "Нужен фильтр"))).map { it.id })
    }

    @Test
    fun refusedFailedStays() {
        // Сервер ответил ошибкой — «Не отправлено» остаётся, пока человек не повторит
        val refused = local("Нужен фильтр").copy(failed = true)
        assertEquals(listOf(8L, refused.id), mergeChat(listOf(refused), listOf(server(8, "Нужен фильтр"))).map { it.id })
    }

    @Test
    fun oneCopyRemovesOneFailed() {
        val a = local("ок", ms = 1000).copy(failed = true, unsure = true)
        val b = local("ок", ms = 2000).copy(failed = true, unsure = true)
        assertEquals(listOf(8L, b.id), mergeChat(listOf(a, b), listOf(server(8, "ок"))).map { it.id })
    }

    @Test
    fun inputClippedToServerLimit() {
        assertEquals("привет", clipChatText("привет"))
        assertEquals(CHAT_MAX_LEN, clipChatText("я".repeat(CHAT_MAX_LEN + 50)).length)
        // Эмодзи (два символа) на границе не рвём пополам
        val clipped = clipChatText("я".repeat(CHAT_MAX_LEN - 1) + "\uD83D\uDE00")
        assertEquals(CHAT_MAX_LEN - 1, clipped.length)
    }
}
