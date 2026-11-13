package com.example.myapplication.laximo

import androidx.compose.animation.core.animate
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import coil.size.Size
import com.example.myapplication.laximo.model.LaximoImageMapItem
import kotlinx.coroutines.launch
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Схема узла + "горячие" зоны из listImageMapByUnit.
 *
 * ✅ pinch-zoom + pan
 * ✅ подсветка выбранного кода
 * ✅ тап по зоне -> onSelectCode(code)
 * ✅ номера поверх схемы (опционально)
 * ✅ auto-focus при выборе детали
 * ✅ double tap -> reset zoom
 * ✅ адаптивный размер номеров при зуме
 *
 * Важно: Координаты Laximo (x1..y2) считаются в системе координат исходного изображения.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun UnitSchemeWithMap(
    imageUrl: String?,
    mapItems: List<LaximoImageMapItem>,
    selectedCode: String? = null,
    showNumbers: Boolean = true,
    onSelectCode: (String) -> Unit = {},
    onInteractingChange: (Boolean) -> Unit = {}
) {
    if (imageUrl.isNullOrBlank()) return

    var boxSize by remember { mutableStateOf(IntSize.Zero) }
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()

    // Не выше 45% экрана: на небольшом телефоне под схемой должен быть виден список деталей
    val schemeHeight = min(420f, LocalConfiguration.current.screenHeightDp * 0.45f).dp

    var scale by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }

    // Сдвиг не дальше краёв увеличенной картинки: без приближения она стоит на месте
    fun clampPan(p: Offset, s: Float): Offset {
        val maxX = boxSize.width * (s - 1f).coerceAtLeast(0f) / 2f
        val maxY = boxSize.height * (s - 1f).coerceAtLeast(0f) / 2f
        return Offset(p.x.coerceIn(-maxX, maxX), p.y.coerceIn(-maxY, maxY))
    }

    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        val newScale = (scale * zoomChange).coerceIn(1f, 4.5f)
        scale = newScale
        pan = clampPan(pan + panChange, newScale)
    }

    // Исходные размеры изображения (от Coil или по координатам)
    var imageSize by remember { mutableStateOf<IntSize?>(null) }
    val srcW = remember(mapItems, imageSize) {
        imageSize?.width?.toFloat() ?: max(1f, mapItems.maxOfOrNull { it.x2 }?.toFloat() ?: 1000f)
    }
    val srcH = remember(mapItems, imageSize) {
        imageSize?.height?.toFloat() ?: max(1f, mapItems.maxOfOrNull { it.y2 }?.toFloat() ?: 1000f)
    }

    suspend fun animateTransform(targetScale: Float, targetPan: Offset) {
        val startScale = scale
        val startPan = pan
        animate(0f, 1f) { fraction, _ ->
            scale = startScale + (targetScale - startScale) * fraction
            pan = Offset(
                startPan.x + (targetPan.x - startPan.x) * fraction,
                startPan.y + (targetPan.y - startPan.y) * fraction
            )
        }
    }

    LaunchedEffect(selectedCode, boxSize) {
        if (!selectedCode.isNullOrBlank() && boxSize.width > 0 && boxSize.height > 0) {
            val itemsWithCode = mapItems.filter { it.code == selectedCode }
            if (itemsWithCode.isEmpty()) return@LaunchedEffect

            val minX = itemsWithCode.minOf { it.x1 }
            val maxX = itemsWithCode.maxOf { it.x2 }
            val minY = itemsWithCode.minOf { it.y1 }
            val maxY = itemsWithCode.maxOf { it.y2 }

            val dstW = boxSize.width.toFloat()
            val dstH = boxSize.height.toFloat()
            val fitScale = min(dstW / srcW, dstH / srcH)
            val offsetX = (dstW - srcW * fitScale) / 2f
            val offsetY = (dstH - srcH * fitScale) / 2f

            val centerX = ((minX + maxX) / 2f) * fitScale + offsetX
            val centerY = ((minY + maxY) / 2f) * fitScale + offsetY

            val targetScale = 2.2f
            val targetPan = Offset(
                (dstW / 2f - centerX) * targetScale,
                (dstH / 2f - centerY) * targetScale
            )
            animateTransform(targetScale, clampPan(targetPan, targetScale))
        }
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(schemeHeight)
            .clip(RectangleShape)
            // Белая подложка: схемы с прозрачным фоном в тёмной теме не читаются
            .background(Color.White)
            .onSizeChanged { boxSize = it }
            .pointerInput(Unit) {
                detectTapGestures(onDoubleTap = { scope.launch { animateTransform(1f, Offset.Zero) } })
            }
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    // Список под схемой замирает, только когда схему двигают: она приближена или на ней два пальца.
                    // Обычное касание без приближения — вертикальный жест уходит списку.
                    var interacting = scale > 1.01f
                    if (interacting) onInteractingChange(true)
                    do {
                        val event = awaitPointerEvent()
                        if (!interacting && event.changes.count { it.pressed } >= 2) {
                            interacting = true
                            onInteractingChange(true)
                        }
                    } while (event.changes.any { it.pressed })
                    if (interacting) onInteractingChange(false)
                }
            }
            // Одним пальцем двигаем только приближенную схему; без приближения жест не перехватываем
            .transformable(transformState, canPan = { scale > 1.01f })
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    translationX = pan.x
                    translationY = pan.y
                    scaleX = scale
                    scaleY = scale
                }
        ) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(imageUrl)
                    .size(Size.ORIGINAL)
                    .build(),
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit,
                onSuccess = { state ->
                    val d = state.result.drawable
                    if (d.intrinsicWidth > 0 && d.intrinsicHeight > 0) {
                        imageSize = IntSize(d.intrinsicWidth, d.intrinsicHeight)
                    }
                }
            )

            if (boxSize.width <= 0 || boxSize.height <= 0) return@Box

            val dstW = boxSize.width.toFloat()
            val dstH = boxSize.height.toFloat()
            val fitScale = min(dstW / srcW, dstH / srcH)

            // Смещения для центрирования изображения (ContentScale.Fit)
            val offsetX = (dstW - srcW * fitScale) / 2f
            val offsetY = (dstH - srcH * fitScale) / 2f

            // 1. Зоны клика
            mapItems.forEach { item ->
                val code = item.code.orEmpty()
                if (code.isBlank()) return@forEach

                val isSelected = !selectedCode.isNullOrBlank() && code == selectedCode

                // Преобразование координат из Laximo в экранные
                val l = item.x1 * fitScale + offsetX
                val t = item.y1 * fitScale + offsetY
                val r = item.x2 * fitScale + offsetX
                val b = item.y2 * fitScale + offsetY

                val wPx = (r - l).coerceAtLeast(1f)
                val hPx = (b - t).coerceAtLeast(1f)

                Box(
                    modifier = Modifier
                        .offset { IntOffset(l.roundToInt(), t.roundToInt()) }
                        .size(width = with(density) { wPx.toDp() }, height = with(density) { hPx.toDp() })
                        .background(if (isSelected) Color(0x33F08A24) else Color.Transparent)
                        .clickableNoRipple { onSelectCode(code) }
                )
            }

            // 2. Номера
            if (showNumbers) {
                val itemsByCode = remember(mapItems) {
                    mapItems.filter { !it.code.isNullOrBlank() }.groupBy { it.code!! }
                }

                itemsByCode.forEach { (code, items) ->
                    val isSelected = !selectedCode.isNullOrBlank() && code == selectedCode

                    // Средняя точка всех вхождений кода
                    val avgX = items.map { (it.x1 + it.x2) / 2f }.average().toFloat()
                    val avgY = items.map { (it.y1 + it.y2) / 2f }.average().toFloat()

                    val cx = avgX * fitScale + offsetX
                    val cy = avgY * fitScale + offsetY

                    NumberBadge(
                        text = code,
                        selected = isSelected,
                        // Читаем scale лениво, в фазе отрисовки — иначе весь список
                        // номеров пересобирался бы заново на каждом кадре пинч-зума.
                        badgeScale = { (1f / scale).coerceIn(0.6f, 1f) },
                        // Точка (cx, cy); по центру её ставит сам значок — ширина у него разная
                        modifier = Modifier.offset { IntOffset(cx.roundToInt(), cy.roundToInt()) }
                    )
                }
            }
        }
    }
}

@Composable
private fun NumberBadge(text: String, selected: Boolean, badgeScale: () -> Float, modifier: Modifier = Modifier) {
    val bg = if (selected) Color(0xFFF08A24) else Color(0xAA1C3A6E)
    Box(
        modifier = modifier
            // Центр значка — в заданной точке, чуть левее и выше (как просили: на 2dp)
            .layout { measurable, constraints ->
                val placeable = measurable.measure(constraints.copy(minWidth = 0, minHeight = 0))
                val shift = 2.dp.roundToPx()
                layout(placeable.width, placeable.height) {
                    placeable.place(-placeable.width / 2 - shift, -placeable.height / 2 - shift)
                }
            }
            .graphicsLayer {
                val s = badgeScale()
                scaleX = s
                scaleY = s
            }
            // «Пилюля»: номера из 4–6 знаков не обрезаются
            .widthIn(min = 24.dp)
            .height(24.dp)
            .background(bg, CircleShape)
            .padding(horizontal = 4.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            color = Color.White,
            style = MaterialTheme.typography.labelSmall,
            maxLines = 1,
            softWrap = false
        )
    }
}

@Composable
private fun Modifier.clickableNoRipple(onClick: () -> Unit): Modifier = this.clickable(
    indication = null,
    interactionSource = remember { MutableInteractionSource() },
    onClick = onClick
)
