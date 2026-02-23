package com.example.myapplication.laximo

import androidx.compose.animation.core.animate
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
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
@Composable
fun UnitSchemeWithMap(
    imageUrl: String?,
    mapItems: List<LaximoImageMapItem>,
    selectedCode: String? = null,
    showNumbers: Boolean = true,
    onSelectCode: (String) -> Unit = {}
) {
    if (imageUrl.isNullOrBlank()) return

    var boxSize by remember { mutableStateOf(IntSize.Zero) }
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()

    var scale by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }

    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        scale = (scale * zoomChange).coerceIn(1f, 4.5f)
        pan += panChange
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
            animateTransform(targetScale, targetPan)
        }
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(320.dp)
            .onSizeChanged { boxSize = it }
            .pointerInput(Unit) {
                detectTapGestures(onDoubleTap = { scope.launch { animateTransform(1f, Offset.Zero) } })
            }
            .transformable(transformState)
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
                model = ImageRequest.Builder(LocalContext.current).data(imageUrl).build(),
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
                        .background(if (isSelected) Color(0x332196F3) else Color.Transparent)
                        .clickableNoRipple { onSelectCode(code) }
                )
            }

            // 2. Номера
            if (showNumbers) {
                val itemsByCode = remember(mapItems) {
                    mapItems.filter { !it.code.isNullOrBlank() }.groupBy { it.code!! }
                }
                val badgeScale = (1f / scale).coerceIn(0.6f, 1f)

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
                        modifier = Modifier
                            .offset { 
                                // Сдвигаем на 12.dp (размер badge / 2), чтобы центр badge совпал с (cx, cy)
                                IntOffset(cx.roundToInt(), cy.roundToInt()) 
                            }
                            .scale(badgeScale)
                    )
                }
            }
        }
    }
}

@Composable
private fun NumberBadge(text: String, selected: Boolean, modifier: Modifier = Modifier) {
    val bg = if (selected) Color(0xFF1E88E5) else Color(0xAA000000)
    Box(
        modifier = modifier
            // Сдвигаем чуть больше влево и вверх (-14dp вместо -12dp), 
            // так как пользователь просил "чуть левее и вверх"
            .offset((-14).dp, (-14).dp)
            .size(24.dp)
            .background(bg, CircleShape),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            color = Color.White,
            style = MaterialTheme.typography.labelSmall,
            maxLines = 1
        )
    }
}

@Composable
private fun Modifier.clickableNoRipple(onClick: () -> Unit): Modifier = this.clickable(
    indication = null,
    interactionSource = remember { MutableInteractionSource() },
    onClick = onClick
)
