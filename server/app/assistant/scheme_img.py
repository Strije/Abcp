"""Схема узла с отмеченной деталью — картинкой для Telegram.

На странице подбора отметку рисует браузер поверх картинки (podbor.html, listImageMapByUnit); в Telegram картинку
показывает сам мессенджер — отметку рисуем на сервере: заливка выноски, рамка и круг вокруг номера позиции.
"""
import io

from PIL import Image, ImageDraw

MAX_SIDE = 1600   # Telegram ужимает фото больше 1280–2560 px; крупнее не нужно


def render(image: bytes, boxes: list[dict]) -> bytes:
    """boxes — выноски позиции из listImageMapByUnit: {x1, y1, x2, y2} в пикселях исходной картинки."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    w = max(3, round(max(im.size) / 300))
    for b in boxes:
        try:
            x1, y1, x2, y2 = (int(float(b[k])) for k in ("x1", "y1", "x2", "y2"))
        except (KeyError, TypeError, ValueError):
            continue
        d.rectangle([x1, y1, x2, y2], fill=(255, 214, 0, 110), outline=(232, 30, 12, 255), width=w)
        cx, cy, r = (x1 + x2) // 2, (y1 + y2) // 2, max(x2 - x1, y2 - y1) // 2 + 6 * w
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(232, 30, 12, 255), width=w)
    out = Image.alpha_composite(im.convert("RGBA"), over).convert("RGB")
    if max(out.size) > MAX_SIDE:
        out.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=88)
    return buf.getvalue()
