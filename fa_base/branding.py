"""Original FA база covers. Rendered locally; no third-party media."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

COLORS = {"welcome": "#f45a7a", "garants": "#eebc78", "check": "#ff707a", "report": "#ff6477", "info": "#e9a5c3", "deal": "#ebc989"}
TITLES = {"welcome": "FA БАЗА", "garants": "ГАРАНТЫ", "check": "ПРОВЕРКА", "report": "ЗАЯВКИ", "info": "О ПРОЕКТЕ", "deal": "СДЕЛКИ"}
CAPTIONS = {
    "welcome": "ПРОВЕРКА • ГАРАНТЫ • СДЕЛКИ",
    "garants": "КАТАЛОГ НЕЗАВИСИМЫХ ПОСРЕДНИКОВ",
    "check": "ПРОВЕРКА ЗАПИСЕЙ И РИСКОВ",
    "report": "ВАШЕ ОБРАЩЕНИЕ УВИДИТ МОДЕРАТОР",
    "info": "ОТКРЫТОСТЬ И ПРОВЕРКА СВЕДЕНИЙ",
    "deal": "СОГЛАСОВАНИЕ УСЛОВИЙ ОБМЕНА",
}
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def color(hex_rgb):
    return tuple(bytes.fromhex(hex_rgb.lstrip("#")))


def font(size, bold=True):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except OSError:
        return ImageFont.load_default(size=max(12, size//5))


@lru_cache(maxsize=6)
def banner_path(kind: str = "welcome") -> str:
    """Build a branded PNG for sendPhoto, cached between requests."""
    if kind not in TITLES:
        kind = "welcome"
    dest = Path("/tmp/fa-baza-covers")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{kind}.png"
    if path.is_file():
        return str(path)
    w, h = 1200, 590
    img = Image.new("RGB", (w, h), "#0a101d")
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            depth = x/w
            stripe = int(19*(1-depth) + 8*y/h)
            pixels[x,y] = (14+stripe, 11+int(7*depth), 26+int(12*depth))
    accent = color(COLORS[kind])
    glow = Image.new("RGBA", (w,h), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((45,80,480,530), fill=(*accent,95))
    gd.ellipse((880,-300,1540,310), fill=(*accent,35))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(110)))
    d = ImageDraw.Draw(img)
    for i in range(3):
        x = 45 + i*135
        d.line((x,35,x-105,h-35), fill=(46,31,52,255), width=2)
    for i in range(4):
        x = 1060 + i*59
        d.line((x,52,x-135,h-50), fill=(73,30,51,255), width=7)
    d.rounded_rectangle((29,27,w-29,h-27),radius=35,outline=(*accent,75),width=2)
    d.rounded_rectangle((83,100,390,479),radius=52,fill=(17,20,35,175),outline=(*accent,110),width=3)
    d.polygon([(235,175),(322,203),(314,307),(234,395),(154,307),(148,203)],
              fill=(29,29,43,255),outline=(*accent,255),width=7)
    d.line([(193,276),(223,304),(281,241)],fill=(*accent,255),width=18,joint="curve")
    d.ellipse((214,296,232,314),fill=(250,243,245,255))
    d.text((462,137),"FA  /  01",fill=(*accent,255),font=font(30))
    d.text((450,225),TITLES[kind],fill=(255,246,247,255),font=font(84))
    d.rounded_rectangle((453,348,1059,354),radius=3,fill=(*accent,230))
    d.text((456,387),CAPTIONS[kind],fill=(241,228,237,255),font=font(22,False))
    d.text((453,483),"Владелец  @mops_garant",fill=(203,182,196,255),font=font(24,False))
    img.convert("RGB").save(path,format="PNG",optimize=True)
    return str(path)
