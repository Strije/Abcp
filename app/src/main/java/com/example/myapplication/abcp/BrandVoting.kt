package com.example.myapplication.abcp

/**
 * Автовыбор бренда голосованием поставщиков — как в Pricer, веб-версии «Проценки»
 * (engine.search_brands + app/search.py choose_brand, github.com/Strije/Pricer).
 * То же на сервере — server/app/brand_vote.py, описание — docs/brand-voting.md.
 *
 * Там каждый поставщик сам называет бренды для номера, и голос — это поставщик. ABCP отдаёт один общий
 * список (search/brands) без разбивки, поэтому голос считаем по предложениям: сколько разных
 * поставщиков продают именно этот номер этого бренда (как ★, offerProvider: склады АвтоДруг — один).
 * Гостю сервер не отдаёт поставщиков — тогда голос это ★ (confirmCount), посчитанная сервером.
 * Плюс упоминания: аналоги пишут настоящего производителя в названии («Фильтр масляный [ан. MAHLE OC90]»).
 *
 * Бренд берём без вопроса, если у него не меньше половины ответивших поставщиков и он в 1,5 раза
 * впереди второго (с упоминаниями). Пороги — те же, что в Pricer, проверены на записи 02.10.2026:
 * 162622 — ZIC у 6 из 12 при 1–2 у остальных — берём ZIC; W71295 — MANN 7+2 против Redskin 4 — MANN;
 * OC90 — MAHLE 7+2 против AM POINT 7 — спрашиваем.
 */

const val BRAND_VOTE_SHARE = 0.5
const val BRAND_VOTE_LEAD = 1.5

/** Сколько брендов из списка ABCP опрашиваем: на каждый — один запрос search/articles. */
const val BRAND_VOTE_MAX = 6

/** Ключ бренда без уточнений в скобках: «MANN-FILTER (China)», «MANN-FILTER» и «MANN» — один бренд. */
fun brandGroupKey(brand: String): String = offerBrandKey(brand.replace(Regex("\\([^)]*\\)"), " "))

data class BrandVote(
    /** Строка списка ABCP, которую открыть (в группе — та, у которой больше поставщиков) */
    val hit: BrandHit,
    /** Разных поставщиков с этим номером этого бренда */
    val votes: Int,
    /** Упоминаний бренда в названиях предложений других брендов */
    val mentions: Int
) {
    val score: Int get() = votes + mentions
}

data class BrandVoting(
    /** Группы брендов, лучшие сверху */
    val votes: List<BrandVote>,
    /** Сколько разных поставщиков продают номер хоть под каким-то брендом */
    val answered: Int
)

/** Разных поставщиков в предложениях; нет данных о поставщиках (гость) — ★ от сервера. */
private fun suppliers(offers: List<Offer>): Int {
    val ids = offers.map(::offerProvider).filter { it.isNotEmpty() }.toSet()
    return if (ids.isNotEmpty()) ids.size else offers.maxOfOrNull { it.confirm } ?: 0
}

/**
 * Голоса по предложениям. offersByBrand — ответ search/articles на каждый опрошенный бренд (номер + бренд);
 * в ответе бывают и аналоги, поэтому в зачёт идут только предложения с тем же номером и брендом группы.
 */
fun voteBrands(number: String, hits: List<BrandHit>, offersByBrand: Map<String, List<Offer>>): BrandVoting {
    val wanted = offerArticleKey(number)
    // одно и то же предложение приходит и в ответе по другому бренду (как аналог) — считаем его один раз
    val exact = offersByBrand.values.flatten()
        .filter { offerArticleKey(it.number) == wanted || offerArticleKey(it.numberFix) == wanted }
        .distinctBy { listOf(offerProvider(it), brandGroupKey(it.brand), it.itemKey.ifBlank { it.description + it.price }) }
    val groups = LinkedHashMap<String, MutableList<BrandHit>>()
    for (hit in hits) {
        val key = brandGroupKey(hit.brand)
        if (key.isNotEmpty()) groups.getOrPut(key) { mutableListOf() }.add(hit)
    }
    val byGroup = exact.groupBy { brandGroupKey(it.brand) }
    val votes = groups.map { (key, members) ->
        val offers = byGroup[key].orEmpty()
        // в группе открываем ту строку ABCP, под которой больше поставщиков
        val best = members.maxWithOrNull(
            compareBy<BrandHit> { h -> suppliers(offers.filter { it.brand.equals(h.brand, ignoreCase = true) }) }
                .thenBy { if (it.available) 1 else 0 }
        ) ?: members.first()
        val words = mentionWords(members)
        val mentions = exact.count { o ->
            brandGroupKey(o.brand) != key && words.any { it.containsMatchIn(o.description.uppercase()) }
        }
        BrandVote(best, suppliers(offers), mentions)
    }
    // при равенстве — порядок ABCP (в наличии выше), как в Pricer — порядок десктопа
    val sorted = votes.withIndex()
        .sortedWith(compareByDescending<IndexedValue<BrandVote>> { it.value.score }.thenByDescending { it.value.votes }.thenBy { it.index })
        .map { it.value }
    val ids = exact.map(::offerProvider).filter { it.isNotEmpty() }.toSet()
    // без данных о поставщиках (гость) — сумма голосов: так большинство набрать труднее, а не легче
    return BrandVoting(sorted, if (ids.isNotEmpty()) ids.size else votes.sumOf { it.votes })
}

/** Слова для поиска упоминаний: бренд целиком и части через «/» («Mahle/Knecht» — MAHLE, KNECHT), от 3 букв. */
private fun mentionWords(members: List<BrandHit>): List<Regex> =
    members.flatMap { h ->
        val full = h.brand.replace(Regex("\\([^)]*\\)"), " ").trim()
        listOf(full) + full.split('/')
    }
        .map { it.trim().uppercase().replace(Regex("\\s+"), " ") }
        .filter { it.count(Char::isLetterOrDigit) >= 3 }
        .distinct()
        .map { Regex("(?<![A-ZА-ЯЁ0-9])" + Regex.escape(it) + "(?![A-ZА-ЯЁ0-9])") }

/** Бренд, который берём без вопроса, или null — тогда показываем список (как choose_brand в Pricer). */
fun pickByVotes(voting: BrandVoting, share: Double = BRAND_VOTE_SHARE, lead: Double = BRAND_VOTE_LEAD): BrandHit? {
    val votes = voting.votes
    if (votes.isEmpty()) return null
    if (votes.size == 1) return votes[0].hit  // все строки ABCP — один бренд
    val first = votes[0]
    val second = votes[1]
    val total = maxOf(voting.answered, 1)
    return if (first.votes >= share * total && first.score >= lead * maxOf(second.score.toDouble(), 0.5)) first.hit else null
}
