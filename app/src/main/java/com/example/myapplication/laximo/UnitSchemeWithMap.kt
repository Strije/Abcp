package com.example.myapplication.laximo

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.foundation.clickable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
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
 *
 * Важно: Координаты Laximo (x1..y2) считаются в системе координат исходного изображения.
 * Мы не всегда знаем исходные размеры, поэтому по умолчанию берём max(x2), max(y2) из mapItems.
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

    // zoom + pan (прикладываем ко всему контенту: и картинка, и зоны, и номера)
    var scale by remember { mutableFloatStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }

    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        scale = (scale * zoomChange).coerceIn(1f, 4.5f)
        pan += panChange
    }

    // Оценка исходных размеров по координатам (лучше, чем фикс 1000x1000)
    val srcW = remember(mapItems) {
        max(1f, mapItems.maxOfOrNull { it.x2 }?.toFloat() ?: 1f)
    }
    val srcH = remember(mapItems) {
        max(1f, mapItems.maxOfOrNull { it.y2 }?.toFloat() ?: 1f)
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(320.dp)
            .onSizeChanged { boxSize = it }
            .transformable(transformState)
    ) {
        // Контент внутри, на который применяется трансформация
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
                    .build(),
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit
            )

            if (boxSize.width <= 0 || boxSize.height <= 0) return@Box

            val dstW = boxSize.width.toFloat()
            val dstH = boxSize.height.toFloat()

            // Fit для исходного изображения
            val fitScale = min(dstW / srcW, dstH / srcH)
            val drawnW = srcW * fitScale
            val drawnH = srcH * fitScale
            val offsetX = (dstW - drawnW) / 2f
            val offsetY = (dstH - drawnH) / 2f

            mapItems.forEach { item ->
                val code = item.code.orEmpty()
                val isSelected = !selectedCode.isNullOrBlank() && code == selectedCode

                val l = item.x1 * fitScale + offsetX
                val t = item.y1 * fitScale + offsetY
                val r = item.x2 * fitScale + offsetX
                val b = item.y2 * fitScale + offsetY

                val wPx = (r - l).coerceAtLeast(1f)
                val hPx = (b - t).coerceAtLeast(1f)

                val wDp: Dp = with(density) { wPx.toDp() }
                val hDp: Dp = with(density) { hPx.toDp() }

                // Прозрачная зона + подсветка выбранного
                Box(
                    modifier = Modifier
                        .offset { IntOffset(l.roundToInt(), t.roundToInt()) }
                        .size(width = wDp, height = hDp)
                        .background(
                            if (isSelected) Color(0x332196F3) else Color.Transparent
                        )
                        .then(
                            if (code.isNotBlank()) {
                                Modifier
                                    .background(
                                        Color.Transparent
                                    )
                                    .clickableNoRipple { onSelectCode(code) }
                            } else Modifier
                        )
                )

                // Номер в центре зоны
                if (showNumbers && code.isNotBlank()) {
                    val cx = ((l + r) / 2f).roundToInt()
                    val cy = ((t + b) / 2f).roundToInt()
                    NumberBadge(
                        text = code,
                        selected = isSelected,
                        modifier = Modifier.offset { IntOffset(cx, cy) }
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
            .offset((-12).dp, (-12).dp)
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

/**
 * Кликабельность без риппла (чтобы не было "пятен" на схеме).
 */
@Composable
private fun Modifier.clickableNoRipple(onClick: () -> Unit): Modifier =
    this.clickable(
        indication = null,
        interactionSource = remember { androidx.compose.foundation.interaction.MutableInteractionSource() },
        onClick = onClick
    )