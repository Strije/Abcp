package com.example.myapplication.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

// Цвета логотипа «Автодруг»: тёмно-синий + оранжевый акцент
val BrandNavy = Color(0xFF1C3A6E)
val BrandNavyLight = Color(0xFF9DB8EC)
val BrandOrange = Color(0xFFF08A24)

/** Срок поставки цветом: сегодня в магазине / 1–3 дня / дольше */
object DeliveryColors {
    // Подобраны под контраст ≥ 4:1 и на белом, и на тёмном фоне (прежний жёлтый #E0A100 на белом был ~2:1)
    val today = Color(0xFF1E8A3C)
    val soon = Color(0xFFB86E00)
    val later = Color(0xFF7A808A)
}

private val Light = lightColorScheme(
    primary = BrandNavy,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCE6FA),
    onPrimaryContainer = Color(0xFF0E2248),
    secondary = BrandOrange,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFFFE3C8),
    onSecondaryContainer = Color(0xFF4A2600),
    background = Color(0xFFF5F6F9),
    surface = Color.White,
    surfaceVariant = Color(0xFFE9ECF2),
    outline = Color(0xFFC4C9D4)
)

private val Dark = darkColorScheme(
    primary = BrandNavyLight,
    onPrimary = Color(0xFF0E2248),
    primaryContainer = Color(0xFF26467D),
    onPrimaryContainer = Color(0xFFDCE6FA),
    secondary = BrandOrange,
    onSecondary = Color(0xFF2B1500),
    secondaryContainer = Color(0xFF6B3A00),
    onSecondaryContainer = Color(0xFFFFE3C8),
    background = Color(0xFF0F1115),
    surface = Color(0xFF171A20),
    surfaceVariant = Color(0xFF23272F),
    outline = Color(0xFF444B57)
)

private val AppShapes = Shapes(
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(20.dp)
)

/** Фирменная тема: светлая/тёмная по системе, без «динамических» цветов обоев — бренд важнее. */
@Composable
fun AvtodrugTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (darkTheme) Dark else Light,
        shapes = AppShapes,
        content = content
    )
}
