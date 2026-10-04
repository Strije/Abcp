package com.example.myapplication

import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.Offer
import com.example.myapplication.abcp.brandGroupKey
import com.example.myapplication.abcp.pickByVotes
import com.example.myapplication.abcp.voteBrands
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Голосование поставщиков за бренд — те же случаи, что в Pricer (запись 02.10.2026). */
class BrandVotingTest {

    private fun hit(brand: String, number: String, available: Boolean = true) = BrandHit(brand, number, "", available)

    private fun offer(brand: String, number: String, distributor: String, description: String = "") =
        Offer(brand, number, cleanNumber(number), description, 100.0, 1, 1, 48, 0, "s$distributor", "$distributor-$brand-$number",
            "", false, distributorId = distributor)

    /** n поставщиков продают номер бренда: d1..dn (сдвиг — чтобы поставщики разных брендов различались) */
    private fun sellers(brand: String, number: String, n: Int, from: Int = 1, description: String = "") =
        (from until from + n).map { offer(brand, number, "d$it", description) }

    private fun cleanNumber(s: String) = com.example.myapplication.abcp.cleanNumber(s)

    @Test
    fun clearMajorityIsPickedWithoutQuestion() {
        // 162622: ABCP отдаёт Acemark и ZIC, оба «в наличии» — список не нужен: ZIC у 6 поставщиков из 7
        val hits = listOf(hit("Acemark", "162622"), hit("ZIC", "162622"))
        val voting = voteBrands("162622", hits, mapOf(
            "Acemark" to sellers("Acemark", "162622", 1, from = 10),
            "ZIC" to sellers("ZIC", "162622", 6) + offer("Acemark", "162622", "d10"),  // аналог из другого ответа не задваивается
        ))
        assertEquals(listOf("ZIC" to 6, "Acemark" to 1), voting.votes.map { it.hit.brand to it.votes })
        assertEquals(7, voting.answered)
        assertEquals("ZIC", pickByVotes(voting)?.brand)
    }

    @Test
    fun mentionsInAnalogNamesCountAndSameBrandRowsMerge() {
        // W71295: MANN-FILTER и MANN-FILTER (China) — один бренд; у Redskin в названии «ан. MANN-FILTER»
        val hits = listOf(hit("KIOSHI", "W71295"), hit("MANN-FILTER (China)", "W71295"), hit("MANN-FILTER", "W71295"),
            hit("Redskin", "W71295"))
        val voting = voteBrands("W71295", hits, mapOf(
            "MANN-FILTER" to sellers("MANN-FILTER", "W71295", 5) + sellers("MANN-FILTER (China)", "W71-295", 2, from = 6),
            "Redskin" to sellers("Redskin", "W71295", 4, from = 20, description = "Фильтр масляный [ан. MANN-FILTER W712/95]")
                .take(2) + sellers("Redskin", "W71295", 2, from = 22),
            "KIOSHI" to sellers("KIOSHI", "W71295", 1, from = 30),
        ))
        val mann = voting.votes.first()
        assertEquals("MANN-FILTER", mann.hit.brand)  // в группе открываем строку, под которой больше поставщиков
        assertEquals(7, mann.votes)
        assertEquals(2, mann.mentions)
        assertEquals("MANN-FILTER", pickByVotes(voting)?.brand)
        assertEquals(brandGroupKey("MANN-FILTER"), brandGroupKey("MANN-FILTER (China)"))
    }

    @Test
    fun closeVoteShowsTheList() {
        // OC90: MAHLE 7 против AM POINT 7 — равенство, спрашиваем
        val hits = listOf(hit("AM POINT", "OC90"), hit("Mahle/Knecht", "OC90"))
        val voting = voteBrands("OC90", hits, mapOf(
            "AM POINT" to sellers("AM POINT", "OC90", 7, from = 10),
            "Mahle/Knecht" to sellers("Mahle/Knecht", "OC90", 7),
        ))
        assertNull(pickByVotes(voting))
        // упоминание «KNECHT» в названии аналога — голос за Mahle/Knecht, но 8 против 7 — всё ещё не перевес в 1,5 раза
        val withMention = voteBrands("OC90", hits, mapOf(
            "AM POINT" to sellers("AM POINT", "OC90", 6, from = 10) + offer("AM POINT", "OC90", "d16", "Фильтр [ан. KNECHT OC90]"),
            "Mahle/Knecht" to sellers("Mahle/Knecht", "OC90", 7),
        ))
        assertEquals(1, withMention.votes.first { it.hit.brand == "Mahle/Knecht" }.mentions)
        assertNull(pickByVotes(withMention))
    }

    @Test
    fun minorityLeaderAndFailedRequestsShowTheList() {
        // лидер есть, но у него меньше половины ответивших
        val hits = listOf(hit("A", "1"), hit("B", "1"), hit("C", "1"), hit("D", "1"))
        val voting = voteBrands("1", hits, mapOf(
            "A" to sellers("A", "1", 3), "B" to sellers("B", "1", 1, from = 10),
            "C" to sellers("C", "1", 1, from = 20), "D" to sellers("D", "1", 2, from = 30),
        ))
        assertEquals("A", voting.votes.first().hit.brand)
        assertNull(pickByVotes(voting))  // 3 из 7 — не большинство
        // ни один запрос предложений не прошёл — решает человек
        assertNull(pickByVotes(voteBrands("1", hits, emptyMap())))
    }

    @Test
    fun guestVotesComeFromServerStars() {
        // гостю сервер не отдаёт поставщиков — только ★ (confirmCount) по каждому бренду+номеру
        fun guest(brand: String, stars: Int, price: Double) =
            Offer(brand, "162622", "162622", "", price, 1, 1, 48, 0, "", "", "", false, confirm = stars)
        val hits = listOf(hit("Acemark", "162622"), hit("ZIC", "162622"))
        val voting = voteBrands("162622", hits, mapOf(
            "ZIC" to listOf(guest("ZIC", 6, 900.0), guest("ZIC", 6, 950.0)),
            "Acemark" to listOf(guest("Acemark", 1, 300.0)),
        ))
        assertEquals(listOf(6, 1), voting.votes.map { it.votes })
        assertEquals(7, voting.answered)
        assertEquals("ZIC", pickByVotes(voting)?.brand)
    }

    @Test
    fun ownPickupWarehousesAreOneSupplier() {
        // склады АвтоДруг (Хрусталёва, ПОР) в ABCP — разные поставщики, но голос один, как у ★
        fun own(distributor: String) = Offer("ZIC", "162622", "162622", "", 900.0, 1, 1, 0, 0, "s$distributor", "k$distributor",
            "", false, deadlineLabel = "Хрусталёва 111 (самовывоз)", distributorId = distributor)
        val voting = voteBrands("162622", listOf(hit("ZIC", "162622"), hit("Acemark", "162622")), mapOf(
            "ZIC" to listOf(own("own1"), own("own2")),
            "Acemark" to sellers("Acemark", "162622", 1, from = 10),
        ))
        assertEquals(1, voting.votes.first { it.hit.brand == "ZIC" }.votes)
        assertNull(pickByVotes(voting))  // 1 против 1 — спрашиваем
    }

    @Test
    fun suppliersCaption() {
        assertEquals("у 1 поставщика", suppliersText(1))
        assertEquals("у 11 поставщиков", suppliersText(11))
        assertEquals("у 21 поставщика", suppliersText(21))
        assertEquals("у 5 поставщиков", suppliersText(5))
    }
}
