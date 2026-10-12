from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def inline(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=value) if is_url else InlineKeyboardButton(text=label, callback_data=value)
         for label, value, is_url in row] for row in rows
    ])


def main_menu(admin: bool = False):
    buttons = [
        [KeyboardButton(text="🔎 Проверить"), KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="💎 Гаранты"), KeyboardButton(text="🤝 Новая сделка")],
        [KeyboardButton(text="⚠️ Сообщить о мошенничестве")],
        [KeyboardButton(text="ℹ️ О проекте"), KeyboardButton(text="📋 Команды")]
    ]
    if admin:
        buttons.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def home_links(cfg):
    return inline(
        [("📣 Наш канал", cfg.channel_url, True)],
        [("⚠️ Сообщить о мошенничестве", "report:start", False)],
        [("💎 Поиск гарантов", cfg.garant_chat_url, True)]
    )


def garants_nav(page, total_pages, cfg):
    return inline(
        [("◀️", f"garants:{max(0, page-1)}", False), (f"{page+1}/{total_pages}", "noop", False),
         ("▶️", f"garants:{min(page+1,total_pages-1)}", False)],
        [("❔ Зачем нужен гарант", "garants:what", False), ("🔎 Чат гарантов", cfg.garant_chat_url, True)]
    )


def admin_menu():
    return inline(
        [("📥 Модерация жалоб", "admin:reports", False)],
        [("💎 Управление гарантами", "admin:garants:0", False),
         ("🗂 Записи базы", "admin:records:0", False)],
        [("➕ Добавить гаранта", "admin:newgarant", False)],
        [("🚫 Добавить мошенника", "admin:newrecord:scam", False),
         ("⚠️ Сомнительные услуги", "admin:newrecord:suspicious", False)],
        [("🤝 Сделки", "admin:deals", False), ("📊 Статистика", "admin:stats", False)]
    )