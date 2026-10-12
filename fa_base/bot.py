"""FA база: Telegram bot, moderation workflows, guarantors and mediated deals."""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, FSInputFile, InputProfilePhotoStatic)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Config
from .db import (Audit, CheckEvent, Deal, Garant, Record, Report, User, ReputationVote,
                 audit, build_db, count, initialize, resolve_user, touch_user)
from .keyboards import home_links, main_menu, admin_menu
from .policies import (MAX_DEALS_PER_DAY, MAX_REPORTS_PER_DAY, identity_matches,
                       deal_status_text, can_cancel_deal, can_finish_deal)
from .utils import evidence_url, mention, parse_amount, safe, username
from .branding import banner_path, avatar_path
from .moderation import ModerationError, review_report

log = logging.getLogger(__name__)
router = Router()
cfg: Config
Sessions = None
BOT_NAME = ""
BOT_READY = False
BOT_ERROR = ""
OWNER = "@mops_garant"
KIND = {"scam": "Подтверждённая запись о мошенничестве", "suspicious": "Сомнительные услуги"}
ASSETS = ("Деньги", "MM2", "Адопт", "Робуксы", "Blade Ball", "Grow Garden", "Другое")


class CheckForm(StatesGroup):
    target = State()


class ReportForm(StatesGroup):
    target = State()
    reason = State()
    evidence = State()


class AddRecord(StatesGroup):
    target = State()
    reason = State()
    evidence = State()


class AddGarant(StatesGroup):
    handle = State()
    country = State()
    channel = State()
    fee = State()


class EditGarant(StatesGroup):
    value = State()


class DealForm(StatesGroup):
    target = State()
    offered = State()
    requested = State()
    amount = State()


def ik(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, **({"url": a[4:]} if a.startswith("url:") else {"callback_data": a}))
         for t, a in row] for row in rows
    ])


def is_admin(uid: int) -> bool:
    return uid in cfg.admin_ids


def cancel_markup():
    return ik([("✖ Отмена", "cancel")])


def report_actions(report_id: int, status: str):
    if status == "pending":
        return ik([("✅ Подтвердить", f"admin:approve:{report_id}"),
                   ("❌ Отклонить", f"admin:reject:{report_id}")],
                  [("📋 Подробности", f"admin:report:{report_id}"),
                   ("📥 Список жалоб", "admin:reports:0")])
    if status in ("approved", "rejected"):
        return ik([("↩️ Отменить решение", f"admin:reopen:{report_id}")],
                  [("📋 Подробности", f"admin:report:{report_id}"),
                   ("📥 Список жалоб", "admin:reports:0")])
    return ik([("📋 Подробности", f"admin:report:{report_id}"),
               ("📥 Список жалоб", "admin:reports:0")])


async def notify_admins(bot: Bot, text: str, markup=None):
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception as ex:
            log.warning("Admin notification %s: %s", admin_id, ex)


async def send_cover(message: Message, kind: str, caption: str, reply_markup=None):
    """Original artwork with graceful fallback to plain text."""
    try:
        await message.answer_photo(FSInputFile(banner_path(kind)), caption=caption[:1024], reply_markup=reply_markup)
    except Exception as exc:
        log.warning("Cover image failed: %s", type(exc).__name__)
        await message.answer(caption, reply_markup=reply_markup, disable_web_page_preview=True)


async def menu(message: Message):
    text = ("<b>FA база — проверка и безопасность сделок</b>\n\n"
            "Привет! Здесь можно проверить записи о пользователе, найти гаранта "
            "и направить жалобу на рассмотрение.\n\n"
            "⚠️ Отсутствие записи <b>не гарантирует</b> безопасность человека. "
            "Жалобы проверяет модератор.\n\n"
            f"👑 Владелец проекта: {mention(OWNER)}\n"
            "🔎 /check @username — поиск по базе\n👤 /me — мой профиль\n💎 /garants — гаранты\n🤝 /deal — предложить сделку")
    await send_cover(message, "welcome", text, reply_markup=home_links(cfg))
    await message.answer("Выберите раздел в меню 👇", reply_markup=main_menu(is_admin(message.from_user.id)))


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    async with Sessions() as s:
        await touch_user(s, message.from_user)
    if command.args and command.args.startswith("check_"):
        try:
            checked_id = int(command.args[6:])
        except ValueError:
            return await message.answer("Неверная ссылка на профиль.")
        return await show_check(message, None, checked_id)
    if command.args and command.args.startswith("checku_"):
        try:
            handle = username(command.args[7:])
        except ValueError:
            return await message.answer("Неверная ссылка на профиль.")
        async with Sessions() as s:
            found_user = await resolve_user(s, handle)
        return await show_check(message, handle, found_user.id if found_user else None)
    if command.args and command.args.startswith("deal_"):
        try:
            deal_id = int(command.args[5:])
        except ValueError:
            return await message.answer("Неверная ссылка на сделку.")
        return await invite_deal(message, bot, deal_id)
    await menu(message)


@router.message(Command("id"))
async def own_id(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help", "commands"))
async def commands(message: Message):
    extra = "\n🔧 /admin, /add_garant, /add_sckam, /add_comn, /binddeal ID — для администраторов" if is_admin(message.from_user.id) else ""
    await message.answer("📋 <b>Команды FA база</b>\n\n"
                         "/start — главное меню\n/check @username — проверить пользователя\n"
                         "/me — проверить себя\n/info — о проекте\n/garants — каталог гарантов\n"
                         "/report — отправить жалобу\n/appeal — обжаловать запись\n"
                         "/deal — создать предложение сделки\n/mydeals — история сделок\n"
                         "/myreports — мои жалобы\n/searchgarant — поиск гаранта\n"
                         "/dealstatus ID — статус сделки\n/canceldeal ID — отменить сделку\n"
                         "/cancel — отменить заполнение" + extra)


async def show_check(message: Message, requested: str | None, requested_id: int | None = None):
    async with Sessions() as s:
        records = list((await s.scalars(select(Record).where(Record.active.is_(True), or_(Record.username == requested if requested else False,
                                             Record.telegram_id == requested_id if requested_id else False)).order_by(Record.created_at.desc()))).all())
        s.add(CheckEvent(checker_id=message.from_user.id, subject_username=requested, subject_id=requested_id))
        await s.commit()
        total_checks = await count(s, CheckEvent, or_(CheckEvent.subject_username == requested if requested else False,
                                                     CheckEvent.subject_id == requested_id if requested_id else False))
        key = f"id:{requested_id}" if requested_id is not None else f"username:{requested}"
        positive = await count(s, ReputationVote, ReputationVote.subject_key == key, ReputationVote.score == 1)
        negative = await count(s, ReputationVote, ReputationVote.subject_key == key, ReputationVote.score == -1)
        if requested_id is not None:
            user = await s.get(User, requested_id)
            if user and not requested:
                requested = user.username
    label = mention(requested) if requested else f"<code>{requested_id}</code>"
    text = f"🔎 <b>Проверка FA база</b>\n\n👤 Пользователь: {label}\n"
    if requested_id:
        text += f"🆔 Telegram ID: <code>{requested_id}</code>\n"
    if records:
        text += "\n⚠️ <b>В базе есть проверенные модератором записи:</b>\n"
        for r in records[:8]:
            text += (f"\n• <b>#{r.id}: {KIND.get(r.kind, r.kind)}</b>\n"
                     f"Причина: {safe(r.reason[:450])}\n"
                     f"Доказательства: {safe(r.evidence_url[:500])}\n")
        if len(records) > 8:
            text += f"\nИ ещё {len(records)-8} записей."
    else:
        text += ("\n✅ <b>Активных записей не найдено.</b>\n"
                 "Это не подтверждение надёжности пользователя. Проверяйте условия сделки самостоятельно.\n")
    text += (f"\n👍 Положительных оценок: {positive} | 👎 Предостережений: {negative}"
             "\n<i>Оценки — мнение пользователей, не подтверждённые факты.</i>")
    text += f"\n🔍 Количество проверок: {total_checks}\n🗓 Проверено: {datetime.now(timezone.utc):%d.%m.%Y} (UTC)"
    link = (f"https://t.me/{BOT_NAME}?start=check_{requested_id}" if requested_id
            else f"https://t.me/{BOT_NAME}?start=checku_{requested}" if requested else "")
    rows = []
    if link:
        rows.append([("🔗 " + ("Ссылка на Telegram ID" if requested_id else "Ссылка по username"), "url:" + link)])
    if requested_id is not None or requested:
        target = f"i:{requested_id}" if requested_id is not None else f"u:{requested}"
        rows.append([("👍 Поддержать", f"vote:plus:{target}"),
                     ("👎 Предостеречь", f"vote:minus:{target}")])
    rows.append([("🔎 Другая проверка", "check:start")])
    rows.append([("📝 Подать апелляцию", "appeal:start")])
    rows.append([("⚠️ Сообщить о мошенничестве", "report:start")])
    await send_cover(message, "check", "🔎 Результат проверки пользователя в FA база. Данные проверяйте самостоятельно.")
    await message.answer(text[:3900], reply_markup=ik(*rows), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("vote:"))
async def vote_callback(call: CallbackQuery):
    try:
        _, val, kind, ident = call.data.split(":", 3)
        score = 1 if val == "plus" else -1 if val == "minus" else None
        if score is None or kind not in ("i", "u"):
            raise ValueError("Некорректная оценка")
        if kind == "i":
            subject = int(ident)
            if subject <= 0:
                raise ValueError("Неверный ID")
            if subject == call.from_user.id:
                return await call.answer("Нельзя оценивать себя", show_alert=True)
            key = f"id:{subject}"
        else:
            subject = username(ident)
            if subject == (call.from_user.username or "").lower():
                return await call.answer("Нельзя оценивать себя", show_alert=True)
            key = f"username:{subject}"
    except (ValueError, TypeError):
        return await call.answer("Неверные данные", show_alert=True)
    async with Sessions() as s:
        vote = await s.scalar(select(ReputationVote).where(
            ReputationVote.voter_id == call.from_user.id,
            ReputationVote.subject_key == key))
        if vote:
            vote.score = score
        else:
            s.add(ReputationVote(voter_id=call.from_user.id,
                                 subject_key=key, score=score))
        await s.commit()
    await call.answer("✅ Оценка сохранена. Обновите проверку, чтобы увидеть результат.", show_alert=True)


@router.message(Command("check"))
async def check_cmd(message: Message, command: CommandObject):
    subject = (command.args or "").strip()
    if message.reply_to_message and not subject:
        u = message.reply_to_message.from_user
        return await show_check(message, u.username.lower() if u.username else None, u.id)
    if not subject:
        return await message.answer("Введите: <code>/check @username</code>, ID пользователя или ответьте командой на его сообщение.")
    if subject.isdigit():
        if int(subject) <= 0:
            return await message.answer("Нужен положительный Telegram ID.")
        return await show_check(message, None, int(subject))
    try:
        u = username(subject)
    except ValueError as ex:
        return await message.answer(safe(ex))
    async with Sessions() as s:
        user = await resolve_user(s, u)
    await show_check(message, u, user.id if user else None)


@router.callback_query(F.data == "check:start")
async def check_start_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(CheckForm.target)
    await call.answer()
    await call.message.answer("🔎 Введите @username или Telegram ID.",
                              reply_markup=cancel_markup())


@router.message(CheckForm.target)
async def check_input(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw:
        return await message.answer("Введите @username или Telegram ID.")
    if raw.isdecimal():
        target_id = int(raw)
        if target_id <= 0:
            return await message.answer("Telegram ID должен быть положительным.")
        target, known_id = None, target_id
    else:
        try:
            target = username(raw)
        except ValueError as exc:
            return await message.answer(safe(exc))
        async with Sessions() as s:
            user = await resolve_user(s, target)
            known_id = user.id if user else None
    await state.clear()
    return await show_check(message, target, known_id)


@router.message(Command("me"))
async def me_cmd(message: Message):
    async with Sessions() as s:
        await touch_user(s, message.from_user)
    await show_check(message, (message.from_user.username or "").lower() or None, message.from_user.id)


@router.message(Command("info"))
async def info_cmd(message: Message):
    async with Sessions() as s:
        scammers = await count(s, Record, Record.kind == "scam", Record.active.is_(True))
        suspicious = await count(s, Record, Record.kind == "suspicious", Record.active.is_(True))
        garants = await count(s, Garant, Garant.active.is_(True))
        reports = await count(s, Report, Report.status == "pending")
    await send_cover(message, "info", "ℹ️ <b>FA база</b>\n\n"
                         "Реестр модерируемых сообщений о рисках при обменах игровыми ценностями.\n\n"
                         f"📌 Подтверждённых записей: {scammers}\n"
                         f"🔸 Сомнительных услуг: {suspicious}\n"
                         f"💎 Гарантов: {garants}\n"
                         f"📨 Жалоб на рассмотрении: {reports}\n\n"
                         f"👑 Владелец: {mention(OWNER)}\n\n"
                         "Записи не заменяют независимую проверку и не служат гарантией сделки.",
                         reply_markup=home_links(cfg))


async def send_garants(message: Message, page: int = 0, edit: bool = False):
    per = 5
    async with Sessions() as s:
        total = await count(s, Garant, Garant.active.is_(True))
        page = max(0, min(page, max(0, (total-1)//per)))
        rows = list((await s.scalars(select(Garant).where(Garant.active.is_(True))
                     .order_by(Garant.id.desc()).offset(page*per).limit(per))).all())
    pages = max(1, (total + per - 1)//per)
    text = f"💎 <b>FA база — каталог гарантов ({total})</b>\n\n"
    if not rows:
        text += "Список пока пуст. Проверенных гарантов добавляет администрация."
    for g in rows:
        text += (f"\n🔹 {mention(g.username)}\n🌍 Страна: {safe(g.country)}\n"
                 f"💰 Комиссия: {safe(g.fee)}\n"
                 f"📝 {safe(g.description[:200]) if g.description else 'Информация уточняется'}\n")
        if g.channel_url:
            text += f"📣 Канал: {safe(g.channel_url)}\n"
    kb = ik([("◀️", f"garants:{max(0,page-1)}"), (f"{page+1}/{pages}", "noop"),
             ("▶️", f"garants:{min(pages-1,page+1)}")],
            [("❔ Зачем нужен гарант", "garants:what")],
            [("🔎 Поиск гарантов", "url:"+cfg.garant_chat_url)])
    if not edit and page == 0:
        await send_cover(message, "garants", "💎 FA база — реестр гарантов. Профили добавляются администрацией.")
    if edit:
        try:
            return await message.edit_text(text[:3900], reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass
    await message.answer(text[:3900], reply_markup=kb, disable_web_page_preview=True)


@router.message(Command("garants"))
async def garants_cmd(message: Message):
    await send_garants(message)


@router.callback_query(F.data.startswith("garants:"))
async def garants_callback(call: CallbackQuery):
    await call.answer()
    if call.data == "garants:what":
        return await call.message.answer("💎 <b>Кто такой гарант?</b>\n\nГарант — независимый посредник, который фиксирует условия обмена и помогает сторонам согласовать передачу ценностей. Наличие гаранта снижает некоторые риски, но не исключает мошенничество. Перед сделкой проверьте профиль, условия, реквизиты и ссылки.")
    await send_garants(call.message, int(call.data.split(":")[1]), edit=True)


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "cancel")
@router.message(Command("cancel"))
async def cancel(event: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer("Отменено")
        await event.message.answer("Действие отменено.")
    else:
        await event.answer("Действие отменено.")


@router.callback_query(F.data == "report:start")
@router.message(Command("report"))
async def report_begin(event: Message | CallbackQuery, state: FSMContext):
    actor_id = event.from_user.id
    async with Sessions() as s:
        recent = await count(s, Report, Report.author_id == actor_id,
                             Report.created_at >= datetime.now(timezone.utc)-timedelta(days=1))
    if recent >= MAX_REPORTS_PER_DAY:
        if isinstance(event, CallbackQuery):
            return await event.answer(f"Лимит: {MAX_REPORTS_PER_DAY} жалоб за 24 часа", show_alert=True)
        return await event.answer(f"Лимит: {MAX_REPORTS_PER_DAY} жалоб за 24 часа")
    await state.clear()
    await state.set_state(ReportForm.target)
    m = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await send_cover(m, "report", "⚠️ <b>Сообщить о мошенничестве</b>\n\nВведите @username пользователя, на которого жалуетесь. Сведения сначала проверит модератор.", reply_markup=cancel_markup())


@router.message(ReportForm.target)
async def report_target(message: Message, state: FSMContext):
    try:
        u = username(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    await state.update_data(target=u)
    await state.set_state(ReportForm.reason)
    await message.answer("Опишите ситуацию одним сообщением (от 15 до 2000 символов):", reply_markup=cancel_markup())


@router.message(ReportForm.reason)
async def report_reason(message: Message, state: FSMContext):
    reason = (message.text or "").strip()
    if not 15 <= len(reason) <= 2000:
        return await message.answer("Нужно от 15 до 2000 символов.")
    await state.update_data(reason=reason)
    await state.set_state(ReportForm.evidence)
    await message.answer("Пришлите HTTPS-ссылку на переписку или доказательства, доступные модератору. Не публикуйте чужие персональные данные.", reply_markup=cancel_markup())


@router.message(ReportForm.evidence)
async def report_evidence(message: Message, state: FSMContext, bot: Bot):
    try:
        evidence = evidence_url(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    d = await state.get_data()
    async with Sessions() as s:
        recent = await count(s, Report, Report.author_id == message.from_user.id,
                             Report.created_at >= datetime.now(timezone.utc)-timedelta(days=1))
        if recent >= MAX_REPORTS_PER_DAY:
            await state.clear()
            return await message.answer("Лимит жалоб на сегодня достигнут. Попробуйте позже.")
        report = Report(author_id=message.from_user.id, target_username=d["target"], reason=d["reason"], evidence_url=evidence)
        s.add(report)
        await s.commit()
        report_id = report.id
    await state.clear()
    await message.answer(f"✅ Жалоба #{report_id} принята на модерацию. Она не появляется в публичной базе автоматически.")
    await notify_admins(bot, f"📨 <b>Новая жалоба #{report_id}</b>\nНа: {mention(d['target'])}\nПричина: {safe(d['reason'][:1000])}\nМатериалы: {safe(evidence)}",
                        report_actions(report_id, "pending"))


@router.callback_query(F.data == "appeal:start")
@router.message(Command("appeal"))
async def appeal(event: Message | CallbackQuery):
    m = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await m.answer("📝 <b>Апелляция</b>\n\nЕсли запись ошибочна, напишите владельцу проекта и приложите ссылку на запись, пояснения и подтверждения. Апелляции проверяются вручную.",
                   reply_markup=ik([("Отправить апелляцию", "url:" + cfg.appeal_url)]))


async def admin_catalog(message: Message, category: str, page: int = 0):
    """Telegram admin screen with paginated rows; IDs are hidden in callback data."""
    page = max(0, min(page, 999))
    model = Garant if category == "garants" else Record
    prefix = "garant" if category == "garants" else "record"
    async with Sessions() as s:
        total = await count(s, model)
        page = min(page, max(0, (total - 1) // 8))
        items = list((await s.scalars(select(model).order_by(model.id.desc())
                      .offset(page * 8).limit(8))).all())
    rowset = []
    for obj in items:
        handle = obj.username or f"ID {obj.telegram_id}"
        flag = "✅" if obj.active else "🚫"
        label = f"{flag} #{obj.id} @{handle}"[:60]
        rowset.append([(label, f"admin:{prefix}:{obj.id}")])
    if not rowset:
        rowset.append([("База пока пуста", "noop")])
    total_pages = max(1, (total + 7) // 8)
    nav = []
    if page > 0:
        nav.append(("◀️", f"admin:{category}:{page - 1}"))
    nav.append((f"{page + 1}/{total_pages}", "noop"))
    if page + 1 < total_pages:
        nav.append(("▶️", f"admin:{category}:{page + 1}"))
    rowset.append(nav)
    rowset.append([("➕ Добавить гаранта" if category == "garants" else
                   "🚫 Добавить мошенника",
                   "admin:newgarant" if category == "garants" else "admin:newrecord:scam")])
    if category == "records":
        rowset.append([("⚠️ Сомнительная услуга", "admin:newrecord:suspicious")])
    rowset.append([("⬅️ Админ-меню", "admin:home")])
    title = "💎 <b>Управление гарантами</b>" if category == "garants" else "🗂 <b>Записи базы</b>"
    await message.answer(f"{title}\nВыберите запись для просмотра или изменения статуса. "
                         f"Всего: {total}", reply_markup=ik(*rowset))


async def admin_report_index(message: Message, page: int = 0):
    page = max(0, min(page, 999))
    async with Sessions() as s:
        total = await count(s, Report)
        page = min(page, max(0, (total - 1) // 8))
        items = list((await s.scalars(select(Report).order_by(Report.id.desc())
                      .offset(page * 8).limit(8))).all())
    icons = {"pending":"⏳", "approved":"✅", "rejected":"❌", "withdrawn":"↪"}
    rows = [[(f"{icons.get(r.status,'📨')} #{r.id} @{r.target_username}"[:60],
              f"admin:report:{r.id}")] for r in items]
    if not rows:
        rows = [[("Жалоб пока нет", "noop")]]
    total_pages = max(1, (total + 7) // 8)
    nav = []
    if page:
        nav.append(("◀️", f"admin:reports:{page-1}"))
    nav.append((f"{page+1}/{total_pages}", "noop"))
    if page+1 < total_pages:
        nav.append(("▶️", f"admin:reports:{page+1}"))
    rows += [nav, [("⬅️ Админ-меню", "admin:home")]]
    await message.answer("📥 <b>Жалобы и решения</b>\n"
                         "⏳ На проверке · ✅ Одобрена · ❌ Отклонена\n"
                         "Нажмите на жалобу, чтобы подтвердить, отклонить или отменить решение.",
                         reply_markup=ik(*rows))


@router.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа к админ-панели.")
    await message.answer("🛠 <b>FA база — админ-панель</b>\n\n"
                         "📥 /reviewreport ID — открыть любую жалобу\n"
                         "🗂 /reports — список последних жалоб\n"
                         "💎 /add_garant, /add_sckam, /add_comn\n"
                         "Скрыть запись: /delete_record ID\n"
                         "Скрыть гаранта: /delete_garant ID\n"
                         "Сделки: /binddeal ID, /finishdeal ID, /disputedeal ID.",
                         reply_markup=admin_menu())


async def admin_render(call: CallbackQuery, text: str, markup=None):
    """Keep navigation inside one message; fall back when the original is not editable."""
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        await call.message.answer(text, reply_markup=markup, disable_web_page_preview=True)


def back_admin(section: str = "home", page: int = 0):
    return [("⬅️ Назад", "admin:home" if section == "home" else f"admin:{section}:{page}")]


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(call: CallbackQuery, bot: Bot, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await call.answer()
    a = call.data.split(":")
    task = a[1] if len(a) > 1 else "home"

    if task == "home":
        return await admin_render(call, "🛠 <b>FA база — управление</b>\n"
            "Выберите раздел. Все действия доступны кнопками.",
            admin_menu())
    if task == "newgarant":
        await state.clear()
        await state.set_state(AddGarant.handle)
        return await call.message.answer(
            "➕ <b>Добавление гаранта</b>\nВведите @username:", reply_markup=cancel_markup())
    if task == "newrecord":
        kind = a[2] if len(a) > 2 else ""
        if kind not in KIND:
            return await call.message.answer("Неизвестный тип записи.")
        await state.clear()
        await state.update_data(kind=kind)
        await state.set_state(AddRecord.target)
        return await call.message.answer(
            f"➕ <b>{safe(KIND[kind])}</b>\nВведите @username:",
            reply_markup=cancel_markup())

    if task == "editgarant" and len(a) == 5:
        gid, field, page_str = a[2], a[3], a[4]
        if not gid.isdecimal() or not page_str.isdecimal() or field not in ("country", "fee", "channel"):
            return await call.message.answer("Неверные параметры редактирования.")
        await state.clear()
        await state.update_data(garant_id=int(gid), edit_field=field, admin_page=int(page_str))
        await state.set_state(EditGarant.value)
        labels = {"country": "страну", "fee": "комиссию", "channel": "ссылку на канал"}
        return await call.message.answer(
            f"✏️ Изменение гаранта #{gid}\nВведите {labels[field]} "
            "(для ссылки можно ввести - чтобы убрать):",
            reply_markup=cancel_markup())

    async with Sessions() as s:
        if task == "stats":
            content = ("📊 <b>Статистика FA база</b>\n"
                f"👤 Пользователей: {await count(s, User)}\n"
                f"🔎 Проверок: {await count(s, CheckEvent)}\n"
                f"💎 Активных гарантов: {await count(s, Garant, Garant.active.is_(True))}\n"
                f"🗂 Публичных записей: {await count(s, Record, Record.active.is_(True))}\n"
                f"📨 Ожидающих жалоб: {await count(s, Report, Report.status == 'pending')}\n"
                f"🤝 Всего сделок: {await count(s, Deal)}")
            return await admin_render(call, content, ik(back_admin()))

        if task in ("reports", "garants", "records", "deals"):
            page = min(max(0, int(a[2])) if len(a)>2 and a[2].isdecimal() else 0, 10000)
            models = {"reports": Report, "garants": Garant, "records": Record, "deals": Deal}
            titles = {"reports": "📨 Жалобы", "garants": "💎 Гаранты",
                      "records": "🗂 Записи базы", "deals": "🤝 Сделки"}
            model = models[task]
            total = await count(s, model)
            pages = max(1, (total + 5 - 1)//5)
            page = min(page, pages-1)
            rows = list((await s.scalars(select(model).order_by(model.id.desc())
                                        .offset(page*5).limit(5))).all())
            buttons = []
            for r in rows:
                if task == "reports":
                    icon = {"pending": "⏳", "approved": "✅",
                            "rejected": "❌", "withdrawn": "↪️"}.get(r.status, "📋")
                    label = f"{icon} #{r.id} @{r.target_username}"
                    callback = f"admin:report:{r.id}:{page}"
                elif task == "garants":
                    label = f"{'💎' if r.active else '🚫'} #{r.id} @{r.username}"
                    callback = f"admin:garant:{r.id}:{page}"
                elif task == "records":
                    label = f"{'🔴' if r.active else '⚪'} #{r.id} @{r.username or r.telegram_id or 'ID'}"
                    callback = f"admin:record:{r.id}:{page}"
                else:
                    label = f"🤝 #{r.id} @{r.target_username} · {deal_status_text(r.status)}"
                    callback = f"admin:deal:{r.id}:{page}"
                buttons.append([(label[:58], callback)])
            if page > 0 or page < pages-1:
                nav = []
                if page > 0: nav.append(("◀", f"admin:{task}:{page-1}"))
                nav.append((f"{page+1}/{pages}", "noop"))
                if page < pages-1: nav.append(("▶", f"admin:{task}:{page+1}"))
                buttons.append(nav)
            if task == "garants":
                buttons.append([("➕ Добавить гаранта", "admin:newgarant")])
            if task == "records":
                buttons.append([("➕ Скамер", "admin:newrecord:scam"),
                                ("➕ Сомнительная", "admin:newrecord:suspicious")])
            buttons.append(back_admin())
            return await admin_render(
                call, f"<b>{titles[task]}</b>\nВсего: {total}. Нажмите на запись для действий.",
                ik(*buttons))

        if task == "report" and len(a) >= 3 and a[2].isdecimal():
            r = await s.get(Report, int(a[2]))
            if not r: return await call.message.answer("Жалоба не найдена.")
            page = int(a[3]) if len(a)>3 and a[3].isdecimal() else 0
            buttons = list(report_actions(r.id, r.status).inline_keyboard)
            buttons.append([InlineKeyboardButton(text="⬅️ К списку жалоб",
                                                  callback_data=f"admin:reports:{page}")])
            content = (f"📨 <b>Жалоба #{r.id}</b> · {safe(r.status)}\n"
                      f"👤 На: {mention(r.target_username)}\n"
                      f"От: <code>{r.author_id}</code>\n"
                      f"Причина: {safe(r.reason)}\n"
                      f"Материалы: {safe(r.evidence_url)}\n\n"
                      "Подтверждение публикует запись в базе; отмена решения её скрывает.")
            return await admin_render(call, content, InlineKeyboardMarkup(inline_keyboard=buttons))

        if task in ("approve", "reject", "reopen") and len(a) >= 3 and a[2].isdecimal():
            try:
                r = await review_report(s, int(a[2]), task, call.from_user.id)
                await s.commit()
                rid, status, author = r.id, r.status, r.author_id
            except ModerationError as exc:
                await s.rollback()
                return await call.message.answer("⚠️ " + safe(str(exc)))
            labels = {"approved": "✅ Подтверждена и опубликована",
                      "rejected": "❌ Отклонена",
                      "pending": "↩️ Решение отменено; запись снята с публикации"}
            await admin_render(call, f"📨 Жалоба #{rid}: {labels[status]}",
                ik(*[[(b.text, b.callback_data) for b in row]
                     for row in report_actions(rid,status).inline_keyboard],
                   back_admin("reports")))
            try:
                await bot.send_message(author,
                     f"FA база: по жалобе #{rid} новый статус — {safe(status)}.")
            except Exception:
                pass
            return

        if task in ("garant", "record", "deal") and len(a) >= 3 and a[2].isdecimal():
            item_id = int(a[2])
            page = int(a[3]) if len(a)>3 and a[3].isdecimal() else 0
            model = {"garant": Garant, "record": Record, "deal": Deal}[task]
            row = await s.get(model,item_id)
            if not row:
                return await call.message.answer("Запись не найдена.")
            section = {"garant": "garants", "record": "records", "deal": "deals"}[task]
            buttons = []
            if task == "garant":
                text = (f"💎 <b>Гарант #{row.id}</b>\n"
                        f"{mention(row.username)}\nСтрана: {safe(row.country)}\n"
                        f"Комиссия: {safe(row.fee)}\n"
                        f"Канал: {safe(row.channel_url or 'не указан')}\n"
                        f"Статус: {'активен' if row.active else 'скрыт'}")
                buttons.extend([
                    [("✏️ Страна",f"admin:editgarant:{row.id}:country:{page}"),
                     ("✏️ Комиссия",f"admin:editgarant:{row.id}:fee:{page}")],
                    [("✏️ Канал",f"admin:editgarant:{row.id}:channel:{page}")],
                    [("🚫 Скрыть" if row.active else "✅ Восстановить",
                       f"admin:confirm:g:{row.id}:{page}:{int(not row.active)}")],
                ])
            elif task == "record":
                text = (f"🗂 <b>Запись #{row.id}</b>\n"
                        f"Аккаунт: {mention(row.username) if row.username else safe(row.telegram_id)}\n"
                        f"Тип: {safe(KIND.get(row.kind,row.kind))}\n"
                        f"Основание: {safe(row.reason)}\n"
                        f"Материалы: {safe(row.evidence_url)}\n"
                        f"Статус: {'активна' if row.active else 'скрыта'}")
                buttons.append([("🚫 Скрыть" if row.active else "✅ Восстановить",
                                f"admin:confirm:r:{row.id}:{page}:{int(not row.active)}")])
            else:
                text = (f"🤝 <b>Сделка #{row.id}</b>\n"
                        f"Участник: {mention(row.target_username)}\n"
                        f"Обмен: {safe(row.offered)} → {safe(row.requested)}\n"
                        f"Сумма: {row.amount} ₽\nСтатус: {safe(deal_status_text(row.status))}")
                if can_finish_deal(row.status):
                    buttons.append([("✅ Завершить",f"admin:confirm:d{row.id}:done:{page}"),
                                    ("⚠️ Спор",f"admin:confirm:d{row.id}:dispute:{page}")])
            buttons.append(back_admin(section,page))
            return await admin_render(call,text,ik(*buttons))

        if task == "confirm" and len(a) in (5, 6):
            if a[2] in ("g", "r") and a[3].isdecimal():
                typ, ident, page = a[2], int(a[3]), int(a[4]) if a[4].isdecimal() else 0
                model = Garant if typ == "g" else Record
                r = await s.get(model, ident)
                if not r:
                    return await call.message.answer("Запись не найдена.")
                section = "garant" if typ == "g" else "record"
                desired = a[5] if len(a) == 6 else str(int(not r.active))
                if desired not in ("0", "1"):
                    return await call.message.answer("Неверный статус.")
                message_text = (("Восстановить" if desired == "1" else "Скрыть")
                                + f" запись #{ident}?")
                actual = f"admin:toggle:{typ}:{ident}:{page}:{desired}"
                back = f"admin:{section}:{ident}:{page}"
            elif a[2].startswith("d") and a[2][1:].isdecimal() and a[3] in ("done", "dispute"):
                ident, operation = int(a[2][1:]), a[3]
                page = int(a[4]) if a[4].isdecimal() else 0
                d = await s.get(Deal, ident)
                if not d or not can_finish_deal(d.status):
                    return await call.message.answer("Сделка недоступна для изменения статуса.")
                message_text = ("Завершить" if operation == "done" else "Открыть спор по") + f" сделке #{ident}?"
                actual = f"admin:dstatus:{ident}:{operation}:{page}"
                back = f"admin:deal:{ident}:{page}"
            else:
                return await call.message.answer("Неверное действие.")
            return await admin_render(call, "⚠️ <b>" + message_text + "</b>",
                                      ik([("✅ Да, выполнить", actual)],
                                         [("⬅️ Отмена", back)]))

        if task == "dstatus" and len(a) == 5 and a[2].isdecimal() and a[3] in ("done", "dispute"):
            ident, operation = int(a[2]), a[3]
            page = int(a[4]) if a[4].isdecimal() else 0
            d = await s.get(Deal, ident)
            if not d or not can_finish_deal(d.status):
                return await call.message.answer("Сделка не найдена или уже завершена.")
            d.status = "completed" if operation == "done" else "disputed"
            s.add(Audit(actor_id=call.from_user.id, action="admin_deal",
                        detail=f"#{ident}:{d.status}"))
            participants = {d.creator_id, d.target_id}
            await s.commit()
            await admin_render(call, f"✅ Сделка #{ident}: {safe(deal_status_text(d.status))}",
                               ik([("↩️ Открыть сделку",f"admin:deal:{ident}:{page}")],
                                  back_admin()))
            for uid in participants:
                if uid:
                    try:
                        await bot.send_message(uid, f"Сделка #{ident}: {safe(deal_status_text(d.status))}")
                    except Exception:
                        pass
            return

        if task == "toggle" and len(a) in (5, 6) and a[3].isdecimal():
            typ,ident,page = a[2],int(a[3]),int(a[4]) if a[4].isdecimal() else 0
            if typ not in ("g","r"):
                return await call.message.answer("Неверный тип.")
            if len(a) != 6 or a[5] not in ("0","1"):
                return await call.message.answer("Кнопка устарела. Откройте запись заново, "
                                                 "чтобы безопасно изменить статус.")
            desired = a[5] == "1"
            model = Garant if typ == "g" else Record
            row = await s.get(model,ident,with_for_update=True)
            if not row:
                return await call.message.answer("Запись не найдена.")
            if row.active != desired:
                row.active = desired
                s.add(Audit(actor_id=call.from_user.id,action="admin_toggle",
                            detail=f"{model.__name__} #{ident}: {desired}"))
                await s.commit()
            return await admin_render(call,
                f"✅ {model.__name__} #{ident}: {'активно' if desired else 'скрыто'}.",
                ik([("↩️ К записи",f"admin:{'garant' if typ=='g' else 'record'}:{ident}:{page}")],
                   back_admin()))

    return await call.message.answer("Неизвестное действие. Вернитесь в /admin.")


@router.message(EditGarant.value)
async def admin_edit_garant_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("Нет доступа.")
    d = await state.get_data()
    field = d.get("edit_field")
    val = (message.text or "").strip()
    if field == "country" and not 1 <= len(val) <= 80:
        return await message.answer("Страна: 1–80 символов.")
    if field == "fee" and not 1 <= len(val) <= 80:
        return await message.answer("Комиссия: 1–80 символов.")
    if field == "channel" and val != "-":
        try:
            val = evidence_url(val)
        except ValueError as exc:
            return await message.answer(safe(exc))
    async with Sessions() as s:
        row = await s.get(Garant,int(d["garant_id"]))
        if not row:
            await state.clear()
            return await message.answer("Гарант не найден.")
        setattr(row, "channel_url" if field == "channel" else field,
                None if field == "channel" and val=="-" else val)
        s.add(Audit(actor_id=message.from_user.id,action="edit_garant",
                    detail=f"#{row.id} {field}"))
        await s.commit()
    await state.clear()
    await message.answer("✅ Данные гаранта обновлены.",
        reply_markup=ik([("💎 Открыть гаранта",
                          f"admin:garant:{d['garant_id']}:{d.get('admin_page',0)}")],
                        [("🛠 Админ-панель", "admin:home")]))


@router.message(Command("reports"))
async def admin_report_list(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа.")
    async with Sessions() as s:
        rows = list((await s.scalars(select(Report).order_by(Report.id.desc()).limit(20))).all())
    if not rows:
        return await message.answer("Жалоб пока нет.")
    icons = {"pending": "⏳", "approved": "✅", "rejected": "❌", "withdrawn": "↪"}
    await message.answer("📥 <b>Последние жалобы</b>",
        reply_markup=ik(*[[(f"{icons.get(r.status, '📋')} #{r.id} @{r.target_username}",
                           f"admin:report:{r.id}")] for r in rows]))


@router.message(Command("reviewreport"))
async def admin_review_by_id(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа.")
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /reviewreport 1")
    async with Sessions() as s:
        r = await s.get(Report, int(command.args.strip()))
        if not r:
            return await message.answer("Жалоба не найдена.")
        content = (f"<b>Жалоба #{r.id}</b> ({safe(r.status)})\n"
                   f"На: {mention(r.target_username)}\n"
                   f"От: <code>{r.author_id}</code>\n"
                   f"Причина: {safe(r.reason)}\n"
                   f"Доказательства: {safe(r.evidence_url)}")
        markup = report_actions(r.id, r.status)
    await message.answer(content, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("withdrawreport"))
async def withdraw_own_report(message: Message, command: CommandObject, bot: Bot):
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /withdrawreport 1")
    async with Sessions() as s:
        try:
            r = await review_report(s, int(command.args.strip()), "withdraw", message.from_user.id)
            await s.commit()
            ident = r.id
        except ModerationError as exc:
            await s.rollback()
            return await message.answer("⚠️ " + safe(str(exc)))
    await message.answer(f"✅ Жалоба #{ident} отозвана до рассмотрения.")
    await notify_admins(bot, f"📋 Жалоба #{ident} отозвана автором.")


@router.message(Command("add_garant"))
async def add_garant_begin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа.")
    await state.set_state(AddGarant.handle)
    await message.answer("💎 Добавление гаранта. Введите @username:", reply_markup=cancel_markup())


@router.message(AddGarant.handle)
async def add_garant_handle(message: Message, state: FSMContext):
    try:
        val = username(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    await state.update_data(handle=val)
    await state.set_state(AddGarant.country)
    await message.answer("Укажите страну (или 'не указана'):")


@router.message(AddGarant.country)
async def add_garant_country(message: Message, state: FSMContext):
    if not message.text or len(message.text) > 80:
        return await message.answer("Максимум 80 символов.")
    await state.update_data(country=message.text)
    await state.set_state(AddGarant.channel)
    await message.answer("Ссылка на канал гаранта (https://...) или '-' без канала:")


@router.message(AddGarant.channel)
async def add_garant_channel(message: Message, state: FSMContext):
    try:
        val = None if message.text == "-" else evidence_url(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    await state.update_data(channel=val)
    await state.set_state(AddGarant.fee)
    await message.answer("Укажите комиссию текстом (например, 50 ₽):")


@router.message(AddGarant.fee)
async def add_garant_fee(message: Message, state: FSMContext):
    fee = (message.text or "").strip()
    if not 1 <= len(fee) <= 80:
        return await message.answer("Введите 1–80 символов.")
    d = await state.get_data()
    async with Sessions() as s:
        existing = await s.scalar(select(Garant).where(Garant.username == d["handle"]))
        if existing:
            existing.active = True
            existing.country = d["country"]
            existing.channel_url = d["channel"]
            existing.fee = fee
        else:
            s.add(Garant(username=d["handle"], country=d["country"], channel_url=d["channel"], fee=fee))
        s.add(Audit(actor_id=message.from_user.id, action="add_garant", detail=d["handle"]))
        await s.commit()
    await state.clear()
    await message.answer(f"✅ Гарант {mention(d['handle'])} сохранён.\n"
                         "Вы можете сразу добавить ещё одного или открыть список.",
                         reply_markup=ik([("➕ Ещё гарант", "admin:newgarant")],
                                         [("💎 Каталог", "admin:garants:0")],
                                         [("⬅️ Админ-меню", "admin:home")]))


@router.message(Command("delete_garant", "delete_record"))
async def hide_record(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа.")
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Укажите числовой ID записи.")
    cls = Garant if message.text.startswith("/delete_garant") else Record
    async with Sessions() as s:
        row = await s.get(cls, int(command.args.strip()))
        if not row:
            return await message.answer("Запись не найдена.")
        row.active = False
        s.add(Audit(actor_id=message.from_user.id, action="hide_entry", detail=f"{cls.__name__} #{row.id}"))
        await s.commit()
    await message.answer("Запись скрыта. В журнале действий она сохраняется.")


@router.message(Command("add_sckam", "add_scam", "add_comn"))
async def record_begin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет доступа.")
    kind = "suspicious" if message.text.split()[0].split('@')[0] == "/add_comn" else "scam"
    await state.update_data(kind=kind)
    await state.set_state(AddRecord.target)
    await message.answer(f"Добавление записи «{safe(KIND[kind])}». Укажите @username:", reply_markup=cancel_markup())


@router.message(AddRecord.target)
async def record_target(message: Message, state: FSMContext):
    try:
        u = username(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    await state.update_data(target=u)
    await state.set_state(AddRecord.reason)
    await message.answer("Напишите основание записи (не менее 15 символов). Только проверенная информация:")


@router.message(AddRecord.reason)
async def record_reason(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    if not 15 <= len(val) <= 2000:
        return await message.answer("Введите 15–2000 символов.")
    await state.update_data(reason=val)
    await state.set_state(AddRecord.evidence)
    await message.answer("Добавьте HTTPS-ссылку на подтверждающие материалы:")


@router.message(AddRecord.evidence)
async def record_evidence(message: Message, state: FSMContext):
    try:
        val = evidence_url(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    d = await state.get_data()
    async with Sessions() as s:
        user = await resolve_user(s, d["target"])
        r = Record(username=d["target"], telegram_id=user.id if user else None,
                   kind=d["kind"], reason=d["reason"], evidence_url=val, created_by=message.from_user.id)
        s.add(r)
        s.add(Audit(actor_id=message.from_user.id, action="add_record", detail=d["target"]))
        await s.commit()
        rid = r.id
    await state.clear()
    await message.answer(f"✅ Запись #{rid} о {mention(d['target'])} добавлена.",
                         reply_markup=ik([("🔍 Открыть запись", f"admin:record:{rid}")],
                                         [("➕ Ещё запись", "admin:newrecord:scam")],
                                         [("⬅️ Админ-меню", "admin:home")]))


def asset_markup(prefix: str):
    pairs = [[(a, f"{prefix}:{i}") for i, a in enumerate(ASSETS[j:j+2], start=j)] for j in range(0, len(ASSETS), 2)]
    return ik(*pairs, [("✖ Отмена", "cancel")])


@router.message(Command("deal"))
async def deal_begin(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    async with Sessions() as s:
        today = await count(s, Deal, Deal.creator_id == message.from_user.id,
                            Deal.created_at >= datetime.now(timezone.utc)-timedelta(days=1))
    if today >= MAX_DEALS_PER_DAY:
        return await message.answer(f"Лимит: {MAX_DEALS_PER_DAY} новые сделки за 24 часа.")
    if command.args:
        try:
            target = username(command.args)
        except ValueError as ex:
            return await message.answer(safe(ex))
        if message.from_user.username and target == message.from_user.username.lower():
            return await message.answer("Нельзя заключить сделку с собой.")
        await state.update_data(target=target)
        await state.set_state(DealForm.offered)
        return await message.answer(f"🤝 Сделка с {mention(target)}\nЧто вы отдаёте?", reply_markup=asset_markup("deal:offered"))
    await state.set_state(DealForm.target)
    await send_cover(message, "deal", "🤝 <b>Новая сделка</b>\nВведите @username другой стороны:", reply_markup=cancel_markup())


@router.message(DealForm.target)
async def deal_target(message: Message, state: FSMContext):
    try:
        u = username(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    if message.from_user.username and u == message.from_user.username.lower():
        return await message.answer("Нельзя заключить сделку с собой.")
    await state.update_data(target=u)
    await state.set_state(DealForm.offered)
    await message.answer(f"Сделка с {mention(u)}. Что вы отдаёте?", reply_markup=asset_markup("deal:offered"))


@router.callback_query(F.data.startswith("deal:offered:"), DealForm.offered)
async def deal_offered(call: CallbackQuery, state: FSMContext):
    i = int(call.data.rsplit(":", 1)[1])
    if i >= len(ASSETS):
        return await call.answer("Ошибка", show_alert=True)
    await state.update_data(offered=ASSETS[i])
    await state.set_state(DealForm.requested)
    await call.answer()
    await call.message.answer("Что хотите получить?", reply_markup=asset_markup("deal:requested"))


@router.callback_query(F.data.startswith("deal:requested:"), DealForm.requested)
async def deal_requested(call: CallbackQuery, state: FSMContext):
    i = int(call.data.rsplit(":", 1)[1])
    if i >= len(ASSETS):
        return await call.answer("Ошибка", show_alert=True)
    await state.update_data(requested=ASSETS[i])
    await state.set_state(DealForm.amount)
    await call.answer()
    await call.message.answer("Введите стоимость сделки целым числом в рублях (минимум 50):", reply_markup=cancel_markup())


@router.message(DealForm.amount)
async def deal_amount(message: Message, state: FSMContext):
    try:
        amount = parse_amount(message.text or "")
    except ValueError as ex:
        return await message.answer(safe(ex))
    d = await state.get_data()
    async with Sessions() as s:
        today = await count(s, Deal, Deal.creator_id == message.from_user.id,
                            Deal.created_at >= datetime.now(timezone.utc)-timedelta(days=1))
        if today >= MAX_DEALS_PER_DAY:
            await state.clear()
            return await message.answer("Лимит новых сделок на сегодня достигнут.")
        known = await resolve_user(s, d["target"])
        deal = Deal(creator_id=message.from_user.id, target_username=d["target"],
                    target_id=known.id if known else None, offered=d["offered"], requested=d["requested"], amount=amount,
                    status="pending_target")
        s.add(deal)
        await s.commit()
        did = deal.id
    await state.clear()
    link = f"https://t.me/{BOT_NAME}?start=deal_{did}"
    await message.answer(f"✅ <b>Сделка #{did} подготовлена</b>\n\n"
                         f"Вторая сторона: {mention(d['target'])}\n"
                         f"Вы отдаёте: {safe(d['offered'])}\nПолучаете: {safe(d['requested'])}\n"
                         f"Сумма: {amount} ₽\n\n"
                         "Перешлите второй стороне персональную ссылку для подтверждения. "
                         "Группу для сделки после согласия участников создаёт администратор.",
                         reply_markup=ik([("📨 Ссылка второй стороне", "url:" + link)]))


async def invite_deal(message: Message, bot: Bot, deal_id: int):
    async with Sessions() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.status != "pending_target":
            return await message.answer("Сделка не найдена или уже не ожидает подтверждения.")
        caller = message.from_user
        if caller.id == d.creator_id:
            return await message.answer("Это ваша сделка: перешлите ссылку другой стороне.")
        # The link may be forwarded; only named counterparty (or previously resolved ID) can accept.
        if not identity_matches(d.target_id, d.target_username, caller.id, caller.username, d.creator_id):
            return await message.answer(f"Ссылка адресована {mention(d.target_username)}. Проверьте аккаунт.")
        text = (f"🤝 <b>Предложение сделки #{d.id}</b>\n"
                f"От участника <code>{d.creator_id}</code>\n"
                f"Вы отдаёте: {safe(d.requested)}\nВы получаете: {safe(d.offered)}\n"
                f"Сумма: {d.amount} ₽\n\n"
                "Проверьте условия перед подтверждением. Администратор создаст группу вручную.")
    await message.answer(text, reply_markup=ik([("✅ Принять", f"dealaccept:{deal_id}")], [("❌ Отказаться", f"dealreject:{deal_id}")]))


@router.callback_query(F.data.startswith("dealaccept:"))
@router.callback_query(F.data.startswith("dealreject:"))
async def deal_answer(call: CallbackQuery, bot: Bot):
    ident = int(call.data.split(":")[1])
    reject = call.data.startswith("dealreject:")
    async with Sessions() as s:
        d = await s.get(Deal, ident)
        if not d or d.status != "pending_target":
            return await call.answer("Сделка недоступна", show_alert=True)
        if not identity_matches(d.target_id, d.target_username, call.from_user.id,
                                call.from_user.username, d.creator_id):
            return await call.answer("Не ваш запрос", show_alert=True)
        d.target_id = call.from_user.id
        d.status = "declined" if reject else "accepted"
        await s.commit()
        creator = d.creator_id
    await call.answer()
    await call.message.answer("❌ Вы отказались от сделки." if reject else "✅ Сделка подтверждена. Ожидайте приглашения от модератора.")
    try:
        await bot.send_message(creator, f"Сделка #{ident}: {'вторая сторона отказалась' if reject else 'вторая сторона согласилась; ожидайте модератора'}.")
    except Exception:
        pass
    if not reject:
        await notify_admins(bot, f"🤝 Сделка #{ident} подтверждена обеими сторонами. Создайте обычную группу, добавьте бота как администратора и отправьте в группе <code>/binddeal {ident}</code>.")


@router.message(Command("binddeal"))
async def bind_group(message: Message, command: CommandObject, bot: Bot):
    if message.chat.type not in ("group", "supergroup"):
        return await message.answer("Эта команда используется внутри группы сделки.")
    if not is_admin(message.from_user.id):
        return await message.answer("Только администратор проекта может привязать группу.")
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /binddeal 123")
    async with Sessions() as s:
        deal = await s.get(Deal, int(command.args.strip()))
        if not deal or deal.status != "accepted" or not deal.target_id:
            return await message.answer("Сделка не подтверждена обеими сторонами или не найдена.")
        existing = await s.scalar(select(Deal).where(Deal.group_chat_id == message.chat.id))
        if existing:
            return await message.answer("Эта группа уже привязана к другой сделке.")
        deal.group_chat_id = message.chat.id
        deal.status = "group_created"
        s.add(Audit(actor_id=message.from_user.id, action="bind_deal", detail=str(deal.id)))
        await s.commit()
        creator, target, did = deal.creator_id, deal.target_id, deal.id
    try:
        link = (await bot.create_chat_invite_link(message.chat.id, member_limit=2)).invite_link
    except Exception:
        link = None
    await message.answer(f"✅ Группа привязана к сделке #{did}.\nУчастники должны проверить все условия до передачи ценностей.\n\nПо завершении модератор пишет /finishdeal {did}; в случае спора /disputedeal {did}.")
    if link:
        for uid in (creator, target):
            try:
                await bot.send_message(uid, f"🔗 Группа сделки #{did}: {safe(link)}")
            except Exception:
                pass
    else:
        await message.answer("⚠️ Не удалось создать ссылку: дайте боту разрешение приглашать пользователей. Пригласите участников вручную.")



@router.message(Command("mydeals"))
async def my_deals(message: Message):
    async with Sessions() as s:
        rows = list((await s.scalars(select(Deal).where(
            or_(Deal.creator_id == message.from_user.id, Deal.target_id == message.from_user.id))
            .order_by(Deal.id.desc()).limit(12))).all())
    if not rows:
        return await message.answer("🤝 У вас пока нет сделок. Новая сделка: /deal")
    lines = [f"#{d.id} · {safe(deal_status_text(d.status))} · {d.amount} ₽" for d in rows]
    await message.answer("🤝 <b>Ваши сделки</b>\n\n" + "\n".join(lines) +
                         "\n\nПодробнее: /dealstatus ID")


@router.message(Command("myreports"))
async def my_reports(message: Message):
    async with Sessions() as s:
        rows = list((await s.scalars(select(Report).where(Report.author_id == message.from_user.id)
                    .order_by(Report.id.desc()).limit(12))).all())
    if not rows:
        return await message.answer("📨 У вас пока нет жалоб. Подать: /report")
    await message.answer("📨 <b>Ваши жалобы</b>\n\n" + "\n".join(
        f"#{r.id} · {safe(r.target_username)} · {safe(r.status)}" for r in rows))


@router.message(Command("dealstatus"))
async def deal_status(message: Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /dealstatus ID")
    async with Sessions() as s:
        deal = await s.get(Deal, int(command.args.strip()))
        if not deal or (not is_admin(message.from_user.id) and
                        message.from_user.id not in (deal.creator_id, deal.target_id)):
            return await message.answer("Сделка не найдена или доступ запрещён.")
        status = deal_status_text(deal.status)
        await message.answer(f"🤝 <b>Сделка #{deal.id}</b>\n\n"
                             f"Статус: {safe(status)}\n"
                             f"Контрагент: {mention(deal.target_username)}\n"
                             f"Выдаётся: {safe(deal.offered)} · Получается: {safe(deal.requested)}\n"
                             f"Сумма: {deal.amount} ₽")


@router.message(Command("canceldeal"))
async def cancel_deal(message: Message, command: CommandObject, bot: Bot):
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /canceldeal ID")
    async with Sessions() as s:
        deal = await s.get(Deal, int(command.args.strip()))
        if not deal or deal.creator_id != message.from_user.id:
            return await message.answer("Отменить можно только собственную сделку.")
        if not can_cancel_deal(deal.status):
            return await message.answer("На этом этапе отмену проводит администратор.")
        deal.status = "cancelled"
        target_id = deal.target_id
        await s.commit()
    await message.answer("✅ Сделка отменена.")
    if target_id:
        try:
            await bot.send_message(target_id, "Вторая сторона отменила сделку.")
        except Exception:
            pass


@router.message(Command("finishdeal", "disputedeal"))
async def finish_deal(message: Message, command: CommandObject, bot: Bot):
    if not is_admin(message.from_user.id):
        return await message.answer("Нет прав модератора.")
    if not command.args or not command.args.strip().isdigit():
        return await message.answer("Формат: /finishdeal ID или /disputedeal ID")
    status = "completed" if message.text.split()[0].split('@')[0] == "/finishdeal" else "disputed"
    async with Sessions() as s:
        deal = await s.get(Deal, int(command.args.strip()))
        if not deal or not can_finish_deal(deal.status):
            return await message.answer("Сделка недоступна для изменения статуса.")
        deal.status = status
        s.add(Audit(actor_id=message.from_user.id, action="deal_status", detail=f"{deal.id}:{status}"))
        await s.commit()
        audience = {deal.creator_id, deal.target_id}
    result_text = f"Сделка #{deal.id}: {deal_status_text(status)}."
    await message.answer("✅ " + result_text)
    for uid in audience:
        if not uid:
            continue
        try:
            await bot.send_message(uid, result_text)
        except Exception:
            pass


@router.message(Command("searchgarant"))
async def search_garant(message: Message, command: CommandObject):
    needle = (command.args or "").strip().removeprefix("@")[:50]
    if len(needle) < 2:
        return await message.answer("Пример: /searchgarant Serbia или /searchgarant nickname")
    async with Sessions() as s:
        found = list((await s.scalars(select(Garant).where(
            Garant.active.is_(True), or_(Garant.username.ilike(f"%{needle}%"),
                                           Garant.country.ilike(f"%{needle}%")))
            .order_by(Garant.id.desc()).limit(15))).all())
    if not found:
        return await message.answer("По запросу ничего не найдено.")
    lines = [f"💎 {mention(g.username)} · {safe(g.country)} · {safe(g.fee)}" for g in found]
    await message.answer("<b>Результаты поиска гарантов</b>\n\n" + "\n".join(lines))


@router.message(F.text)
async def text_menu(message: Message, state: FSMContext):
    if message.text == "🛠 Админ-панель":
        if not is_admin(message.from_user.id):
            return await message.answer("Нет доступа к админ-панели.")
        return await message.answer("🛠 <b>FA база — управление</b>\n"
                                    "Выбирайте раздел кнопками ниже.",
                                    reply_markup=admin_menu())
    if message.text == "🔎 Проверить":
        await state.clear()
        await state.set_state(CheckForm.target)
        return await message.answer(
            "🔎 <b>Кого проверить?</b>\n"
            "Пришлите @username или числовой Telegram ID.\n"
            "Команда /check также продолжает работать.",
            reply_markup=cancel_markup())
    if message.text == "👤 Мой профиль":
        return await me_cmd(message)
    if message.text == "💎 Гаранты":
        return await send_garants(message)
    if message.text == "🤝 Новая сделка":
        return await deal_begin(message, state, CommandObject(prefix='/',command='deal',args=None))
    if message.text == "⚠️ Сообщить о мошенничестве":
        return await report_begin(message, state)
    if message.text == "ℹ️ О проекте":
        return await info_cmd(message)
    if message.text == "📋 Команды":
        return await commands(message)



async def ensure_bot_avatar(bot: Bot):
    """Apply FA база avatar once; persist successful uploads across redeploys."""
    version = "fa-brand-avatar-v1"
    async with Sessions() as s:
        existing = await s.scalar(select(Audit.id).where(
            Audit.action == "bot_avatar_applied", Audit.detail == version).limit(1))
    if existing:
        return
    try:
        await bot.set_my_profile_photo(
            photo=InputProfilePhotoStatic(photo=FSInputFile(avatar_path())))
    except Exception as exc:
        log.warning("Failed to update FA база avatar: %s", type(exc).__name__)
        return
    async with Sessions() as s:
        s.add(Audit(actor_id=0, action="bot_avatar_applied", detail=version))
        await s.commit()
    print("FA база: custom Telegram profile avatar installed", flush=True)


async def setup_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="check", description="Проверка по @username или ID"),
        BotCommand(command="me", description="Проверить себя"),
        BotCommand(command="info", description="Информация о проекте"),
        BotCommand(command="garants", description="Каталог гарантов"),
        BotCommand(command="report", description="Отправить жалобу"),
        BotCommand(command="deal", description="Предложить сделку"),
        BotCommand(command="appeal", description="Обжаловать запись"),
        BotCommand(command="mydeals", description="Мои сделки"),
        BotCommand(command="searchgarant", description="Поиск гарантов"),
        BotCommand(command="myreports", description="Мои жалобы")
    ])


dispatcher = Dispatcher()
dispatcher.include_router(router)


async def run():
    global cfg, Sessions, BOT_NAME, BOT_READY, BOT_ERROR
    BOT_READY = False
    BOT_ERROR = ""
    cfg = Config.load()
    engine, Sessions = build_db(cfg.database_url)
    await initialize(engine)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await bot.get_me()
        BOT_NAME = me.username
        try:
            await bot.set_my_name(name="FA база")
            await bot.set_my_description(description=(
                "FA база — проверка записей о пользователях, каталог гарантов, "
                "жалобы на модерацию и согласование безопасных сделок. Владелец @mops_garant."))
            await bot.set_my_short_description(short_description=(
                "FA база: проверка аккаунтов, гаранты и сделки. Владелец @mops_garant."))
        except Exception as exc:
            log.warning("Bot profile metadata not updated: %s", type(exc).__name__)
        await ensure_bot_avatar(bot)
        await setup_commands(bot)
        BOT_READY = True
        print(f"FA база: Telegram bot @{BOT_NAME} ready for polling", flush=True)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    except Exception as exc:
        BOT_ERROR = type(exc).__name__
        print(f"FA база: Telegram polling error ({BOT_ERROR})", flush=True)
        raise
    finally:
        BOT_READY = False
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())