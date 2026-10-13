"""Original FA база status artwork, dependent on real reputation vote counts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
THEMES = {
    "neutral": ((88, 104, 134), "ПОЛЬЗОВАТЕЛЬ", "Проверка записей сообщества"),
    "scam": ((236, 45, 69), "МОШЕННИК", "Будьте осторожны!"),
    "garant": ((242, 141, 190), "ГАРАНТ", "Проверка статуса гаранта"),
}


def font(px: int, bold: bool = True):
    try:
        return ImageFont.truetype(BOLD if bold else FONT, px)
    except OSError:
        return ImageFont.load_default()


def reviews_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "оценка"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "оценки"
    return "оценок"


@lru_cache(maxsize=128)
def check_banner_path(role: str, opinion_count: int) -> str:
    role = role if role in THEMES else "neutral"
    n = max(0, int(opinion_count))
    path = Path("/tmp/fa-baza-covers") / f"check-{role}-{n}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return str(path)
    accent, title, subtitle = THEMES[role]
    w, h = 1200, 590
    image = Image.new("RGB", (w, h), (15, 19, 31))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        f = y / h
        draw.line((0, y, w, y), fill=(int(24+9*f), int(27+6*f), int(42+10*f)))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-250, -190, 650, 740), fill=(*accent, 85))
    gd.ellipse((790, -260, 1450, 380), fill=(*accent, 50))
    image = Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(100)))
    d = ImageDraw.Draw(image)
    d.polygon([(850, 0), (1200, 0), (1200, 590), (1030, 590)],
              fill=(int(24+accent[0]*0.22), int(27+accent[1]*0.2), int(42+accent[2]*0.2), 255))
    d.rounded_rectangle((32, 30, 1168, 558), radius=36, outline=(*accent, 170), width=4)
    d.ellipse((74, 103, 402, 431), outline=(*accent, 230), width=11)
    d.polygon([(237, 134), (328, 176), (319, 304), (237, 381), (152, 304), (143, 176)],
              fill=(24, 25, 39, 255), outline=(*accent, 255), width=11)
    if role == "scam":
        d.line([(194, 236), (280, 315)], fill=(255, 250, 250, 255), width=19)
        d.line([(280, 236), (194, 315)], fill=(255, 250, 250, 255), width=19)
    else:
        d.line([(184, 263), (220, 297), (298, 219)],
               fill=(255, 246, 250, 255), width=19, joint="curve")
    d.text((440, 105), "FA БАЗА  /  ПРОВЕРКА", fill=(*accent, 255), font=font(37))
    title_size = 79
    while d.textbbox((0, 0), title, font=font(title_size))[2] > 680 and title_size > 35:
        title_size -= 2
    d.text((438, 194), title, fill=(250, 247, 250, 255), font=font(title_size))
    d.text((440, 313), subtitle, fill=(235, 228, 235, 255), font=font(30, False))
    d.rounded_rectangle((439, 398, 1050, 413), radius=7, fill=(69, 69, 83, 230))
    progress = min(1.0, n / 25.0)
    if progress > 0:
        d.rounded_rectangle((439, 398, 439 + max(14, int(611*progress)), 413),
                            radius=7, fill=(*accent, 255))
    count_text = f"{n:,}".replace(",", " ") + f" {reviews_word(n)}"
    count_font = 40
    while d.textbbox((0, 0), count_text, font=font(count_font))[2] > 640 and count_font > 18:
        count_font -= 2
    d.text((440, 449), count_text, fill=(255, 248, 251, 255), font=font(count_font))
    d.text((440, 523), "@mops_garant", fill=(220, 188, 205, 255), font=font(21, False))
    image.convert("RGB").save(path, quality=92, optimize=True)
    return str(path)
