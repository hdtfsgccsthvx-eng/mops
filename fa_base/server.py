"""Password-protected web dashboard, health check and bot process for Railway."""
from __future__ import annotations
import asyncio
import os
import secrets
from contextlib import asynccontextmanager
import sys
from datetime import datetime
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select, func

from .db import Audit, Deal, Garant, Record, Report, User, CheckEvent, build_db, initialize, count
from .utils import username, evidence_url


engine = None
Sessions = None
running_bot = None
signing_secret = os.getenv("SESSION_SECRET", "")
signer = URLSafeTimedSerializer(signing_secret, salt="fa-baza-session") if signing_secret else None
csrf_signer = URLSafeTimedSerializer(signing_secret, salt="fa-baza-csrf") if signing_secret else None


@asynccontextmanager
async def lifespan(app):
    global engine, Sessions, running_bot
    engine, Sessions = build_db(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fa_baza.db"))
    await initialize(engine)
    if os.getenv("BOT_TOKEN") and os.getenv("ADMIN_IDS"):
        from .bot import run
        running_bot = asyncio.create_task(run())
        running_bot.add_done_callback(lambda t: print("Bot task stopped:", t.exception() if not t.cancelled() else "cancelled", flush=True))
    else:
        print("FA база: bot is not started; BOT_TOKEN and/or ADMIN_IDS is missing", flush=True)
    yield
    if running_bot:
        running_bot.cancel()
        try:
            await running_bot
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="FA база — админ-панель", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
STYLE = """<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b111e;color:#e7eaf2;font:15px system-ui,Arial,sans-serif}
a{color:#f28c9d}.wrap{max-width:1180px;margin:auto;padding:30px 18px}h1{font-size:30px}h2{font-size:19px}
nav{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}nav a,.button{color:#fff;background:#293445;text-decoration:none;border-radius:10px;padding:9px 15px}
.card{background:#172335;border:1px solid #2d384a;border-radius:16px;padding:20px;margin:18px 0;box-shadow:0 10px 30px #0002}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}.kpi{background:#19293a;border-radius:12px;padding:16px}.kpi b{font-size:25px;display:block;color:#fa8ca1}
input,textarea,select{width:100%;border:1px solid #405064;background:#0e1727;color:#fff;padding:11px;border-radius:9px;margin:6px 0 14px}
button{background:#ce506f;color:white;border:0;padding:10px 16px;border-radius:9px;cursor:pointer;font-weight:600}
button.danger{background:#7f3844}.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
table{border-collapse:collapse;width:100%;display:block;overflow:auto}th,td{border-bottom:1px solid #324053;padding:12px 10px;text-align:left;vertical-align:top;max-width:360px;overflow-wrap:anywhere}th{color:#a9b9cf}
.badge{padding:4px 8px;border-radius:8px;background:#34445b;font-size:12px}.error{color:#ff9aac}.muted{color:#9baabe}.inline{display:inline}details{margin:8px 0}summary{cursor:pointer}form.inline button{padding:7px 12px;margin:2px}
</style>"""


def esc(v):
    return escape(str(v if v is not None else ""), quote=True)


def page(title: str, body: str, nav=True):
    top = '<nav><a href="/admin">Обзор</a><a href="/admin/garants">Гаранты</a><a href="/admin/records">Записи базы</a><a href="/admin/reports">Жалобы</a><a href="/admin/deals">Сделки</a><a href="/admin/logout">Выход</a></nav>' if nav else ''
    return HTMLResponse(f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} • FA база</title>{STYLE}<div class="wrap"><h1>◆ FA база</h1>{top}<main>{body}</main></div></html>')


def get_admin(request):
    if not signer or not os.getenv("WEB_ADMIN_PASSWORD"):
        return False
    value = request.cookies.get("fa_admin")
    if not value:
        return False
    try:
        payload = signer.loads(value, max_age=86400)
        return payload == "authorized"
    except (BadSignature, SignatureExpired):
        return False


def require_admin(request):
    if not get_admin(request):
        raise HTTPException(status_code=403, detail="Требуется вход администратора")


def csrf_token():
    if not csrf_signer:
        return ""
    return csrf_signer.dumps("form")


def check_csrf(request, token):
    require_admin(request)
    try:
        if not csrf_signer or csrf_signer.loads(token, max_age=7200) != "form":
            raise ValueError("CSRF")
    except (BadSignature, SignatureExpired, ValueError):
        raise HTTPException(status_code=403, detail="Истекла сессия формы, обновите страницу")


def csrf_input():
    return f'<input type="hidden" name="csrf" value="{esc(csrf_token())}">'


def redirect(url):
    return RedirectResponse(url, status_code=303)


@app.get("/health")
async def health():
    bot_module = sys.modules.get("fa_base.bot")
    configured = bool(os.getenv("BOT_TOKEN") and os.getenv("ADMIN_IDS"))
    running = bool(running_bot and not running_bot.done())
    ready = bool(running and bot_module and getattr(bot_module, "BOT_READY", False))
    return {"status": "ok" if ready else "degraded",
            "bot_configured": configured, "bot_running": running,
            "bot_ready": ready,
            "bot_username": getattr(bot_module, "BOT_NAME", None) if ready else None,
            "bot_error": getattr(bot_module, "BOT_ERROR", "") or None}


@app.get("/")
async def home():
    return redirect("/admin")


@app.get("/admin/login")
async def login_get():
    return page("Вход", '<div class="card" style="max-width:420px"><h2>Вход администратора</h2><form method="post" action="/admin/login"><label>Логин</label><input name="username" autocomplete="username" required><label>Пароль</label><input name="password" type="password" autocomplete="current-password" required><button>Войти</button></form></div>', nav=False)


@app.post("/admin/login")
async def login_post(username: str = Form(), password: str = Form()):
    good = (bool(signer) and bool(os.getenv("WEB_ADMIN_PASSWORD"))
            and secrets.compare_digest(username, os.getenv("WEB_ADMIN_USER", "admin"))
            and secrets.compare_digest(password, os.getenv("WEB_ADMIN_PASSWORD", "")))
    if not good:
        return page("Вход", '<div class="card error">Неправильный логин или пароль. Проверьте WEB_ADMIN_USER и WEB_ADMIN_PASSWORD в Railway.</div><a href="/admin/login">Попробовать снова</a>', nav=False)
    result = redirect("/admin")
    result.set_cookie("fa_admin", signer.dumps("authorized"), httponly=True, secure=True, samesite="lax", max_age=86400)
    return result


@app.get("/admin/logout")
async def logout():
    response = redirect("/admin/login")
    response.delete_cookie("fa_admin")
    return response


@app.get("/admin")
async def dashboard(request: Request):
    if not get_admin(request):
        return redirect("/admin/login")
    async with Sessions() as s:
        numbers = [("Пользователи", await count(s, User)), ("Проверки", await count(s, CheckEvent)),
                   ("Гаранты", await count(s, Garant, Garant.active.is_(True))),
                   ("Записи", await count(s, Record, Record.active.is_(True))),
                   ("Ожидают проверки", await count(s, Report, Report.status == 'pending')),
                   ("Сделки", await count(s, Deal))]
    tiles = ''.join(f'<div class="kpi"><b>{v}</b>{esc(k)}</div>' for k,v in numbers)
    return page("Обзор", f'<h2>Панель управления</h2><div class="kpis">{tiles}</div><div class="card"><p>Владелец проекта: <b>@mops_garant</b></p><p>Редактируйте список гарантов и записи, проверяйте жалобы и отслеживайте сделки.</p><p class="muted">Все жалобы поступают в закрытую модерацию и появляются в базе только после ручного одобрения.</p></div>')


@app.get("/admin/garants")
async def garants(request: Request):
    if not get_admin(request):return redirect("/admin/login")
    async with Sessions() as s:
        items = (await s.scalars(select(Garant).order_by(Garant.id.desc()).limit(200))).all()
    trs = ''.join(f'<tr><td>#{g.id}</td><td>@{esc(g.username)}</td><td>{esc(g.country)}</td><td>{esc(g.channel_url)}</td><td>{esc(g.fee)}</td><td>{"Активен" if g.active else "Скрыт"}</td><td><form class="inline" method="post" action="/admin/garants/{g.id}/toggle">{csrf_input()}<button>{"Скрыть" if g.active else "Включить"}</button></form></td></tr>' for g in items)
    form = f'''<div class="card"><h2>Добавить или обновить гаранта</h2><form method="post" action="/admin/garants">{csrf_input()}<div class="formgrid"><label>Telegram username<input name="handle" placeholder="username" required></label><label>Страна<input name="country" required></label><label>Канал HTTPS<input name="channel" placeholder="https://t.me/..."></label><label>Комиссия<input name="fee" value="По договорённости" required></label></div><label>Описание<input name="description" maxlength="400"></label><button>Сохранить гаранта</button></form></div>'''
    return page("Гаранты",form+f'<div class="card"><h2>Каталог гарантов</h2><table><tr><th>ID</th><th>Профиль</th><th>Страна</th><th>Канал</th><th>Комиссия</th><th>Статус</th><th>Действие</th></tr>{trs}</table></div>')


@app.post("/admin/garants")
async def add_garant(request: Request, csrf: str = Form(), handle: str = Form(), country: str = Form(), channel: str = Form(default=""), fee: str = Form(), description: str = Form(default="")):
    check_csrf(request,csrf)
    try:
        u = username(handle)
        c = evidence_url(channel) if channel else None
    except ValueError as e:
        raise HTTPException(400,str(e))
    if len(country)>80 or len(fee)>80 or len(description)>400:
        raise HTTPException(400,"Превышена длина поля")
    async with Sessions() as s:
        row = await s.scalar(select(Garant).where(Garant.username==u))
        if not row:
            row=Garant(username=u,country=country,fee=fee)
            s.add(row)
        row.country=country;row.channel_url=c;row.fee=fee;row.description=description;row.active=True
        s.add(Audit(actor_id=0,action="web_upsert_garant",detail=u))
        await s.commit()
    return redirect("/admin/garants")


@app.post("/admin/garants/{gid}/toggle")
async def toggle_garant(gid:int, request:Request,csrf:str=Form()):
    check_csrf(request,csrf)
    async with Sessions() as s:
        row=await s.get(Garant,gid)
        if not row:raise HTTPException(404)
        row.active=not row.active
        s.add(Audit(actor_id=0,action="web_toggle_garant",detail=f"{gid}:{row.active}"))
        await s.commit()
    return redirect("/admin/garants")


@app.get("/admin/records")
async def records(request:Request):
    if not get_admin(request):return redirect("/admin/login")
    async with Sessions() as s:
        items=(await s.scalars(select(Record).order_by(Record.id.desc()).limit(200))).all()
    trs=''.join(f'<tr><td>#{r.id}</td><td>@{esc(r.username)}</td><td>{"Мошенничество" if r.kind=="scam" else "Сомнительное"}</td><td>{esc(r.reason)}</td><td>{esc(r.evidence_url)}</td><td>{"Активна" if r.active else "Скрыта"}</td><td><form class="inline" method="post" action="/admin/records/{r.id}/toggle">{csrf_input()}<button>{"Скрыть" if r.active else "Включить"}</button></form></td></tr>' for r in items)
    f=f'''<div class="card"><h2>Новая запись модерации</h2><form method="post" action="/admin/records">{csrf_input()}<div class="formgrid"><label>Username<input name="handle" required></label><label>Категория<select name="kind"><option value="scam">Подтверждённое мошенничество</option><option value="suspicious">Сомнительные услуги</option></select></label><label>Ссылка на доказательства<input name="evidence" type="url" required></label></div><label>Причина<textarea name="reason" minlength="15" maxlength="2000" required></textarea></label><button>Добавить запись</button></form></div>'''
    return page("Реестр", f +f'<div class="card"><h2>Записи базы</h2><table><tr><th>ID</th><th>Username</th><th>Категория</th><th>Причина</th><th>Доказательства</th><th>Статус</th><th>Действие</th></tr>{trs}</table></div>')


@app.post("/admin/records")
async def create_record(request:Request,csrf:str=Form(),handle:str=Form(),kind:str=Form(),reason:str=Form(),evidence:str=Form()):
    check_csrf(request,csrf)
    if kind not in ('scam','suspicious') or not 15 <= len(reason) <= 2000:
        raise HTTPException(400,"Некорректные сведения")
    try:
        u=username(handle); e=evidence_url(evidence)
    except ValueError as ex:raise HTTPException(400,str(ex))
    async with Sessions() as s:
        known=await s.scalar(select(User).where(User.username==u))
        s.add(Record(username=u,telegram_id=known.id if known else None,kind=kind,reason=reason,evidence_url=e,created_by=0))
        s.add(Audit(actor_id=0,action='web_add_record',detail=u))
        await s.commit()
    return redirect('/admin/records')


@app.post("/admin/records/{rid}/toggle")
async def toggle_record(rid:int,request:Request,csrf:str=Form()):
    check_csrf(request,csrf)
    async with Sessions() as s:
        row=await s.get(Record,rid)
        if not row:raise HTTPException(404)
        row.active=not row.active
        s.add(Audit(actor_id=0,action='web_toggle_record',detail=f'{rid}:{row.active}'))
        await s.commit()
    return redirect('/admin/records')


@app.get('/admin/reports')
async def reports(request:Request):
    if not get_admin(request):return redirect('/admin/login')
    async with Sessions() as s:
        items=(await s.scalars(select(Report).order_by(Report.id.desc()).limit(200))).all()
    trs=''
    for r in items:
        actions=(f'<form class="inline" method="post" action="/admin/reports/{r.id}/approve">{csrf_input()}<button>Подтвердить</button></form>'
                 f'<form class="inline" method="post" action="/admin/reports/{r.id}/reject">{csrf_input()}<button class="danger">Отклонить</button></form>') if r.status=='pending' else ''
        trs+=f'<tr><td>#{r.id}</td><td>@{esc(r.target_username)}</td><td>{esc(r.reason)}</td><td>{esc(r.evidence_url)}</td><td><span class="badge">{esc(r.status)}</span></td><td>{actions}</td></tr>'
    return page('Жалобы',f'<div class="card"><h2>Модерация жалоб</h2><p class="muted">Одобряйте запись только после проверки доказательств. В публичной базе не должно быть необоснованных обвинений.</p><table><tr><th>ID</th><th>Пользователь</th><th>Описание</th><th>Доказательства</th><th>Статус</th><th>Модерация</th></tr>{trs}</table></div>')


@app.post('/admin/reports/{rid}/{action}')
async def review_report(rid:int,action:str,request:Request,csrf:str=Form()):
    check_csrf(request,csrf)
    if action not in ('approve','reject'):raise HTTPException(404)
    async with Sessions() as s:
        r=await s.get(Report,rid)
        if not r or r.status!='pending':raise HTTPException(409,'Жалоба уже рассмотрена')
        if action=='approve':
            u=await s.scalar(select(User).where(User.username==r.target_username))
            s.add(Record(username=r.target_username,telegram_id=u.id if u else None,kind='scam',reason=r.reason,evidence_url=r.evidence_url,created_by=0))
            r.status='approved'
        else:r.status='rejected'
        r.reviewed_by=0
        s.add(Audit(actor_id=0,action='web_review_report',detail=f'{rid}:{r.status}'))
        await s.commit()
    return redirect('/admin/reports')


@app.get('/admin/deals')
async def deals(request:Request):
    if not get_admin(request):return redirect('/admin/login')
    async with Sessions() as s:
        items=(await s.scalars(select(Deal).order_by(Deal.id.desc()).limit(200))).all()
    from .policies import can_finish_deal, deal_status_text
    trs = ''
    for d in items:
        actions = ''
        if can_finish_deal(d.status):
            actions = (f'<form class="inline" method="post" action="/admin/deals/{d.id}/complete">{csrf_input()}<button>Завершить</button></form>'
                       f'<form class="inline" method="post" action="/admin/deals/{d.id}/dispute">{csrf_input()}<button class="danger">Открыть спор</button></form>')
        trs += (f'<tr><td>#{d.id}</td><td>{esc(d.creator_id)}</td><td>@{esc(d.target_username)}</td>'
                f'<td>{esc(d.offered)} → {esc(d.requested)}</td><td>{d.amount} ₽</td>'
                f'<td>{esc(deal_status_text(d.status))}</td><td>{esc(d.group_chat_id or "—")}</td><td>{actions}</td></tr>')
    return page('Сделки',f'<div class="card"><h2>Сделки</h2><p>После подтверждения обеих сторон создайте группу, добавьте бота администратором и выполните <code>/binddeal ID</code>. Ни бот, ни администратор не удерживают платежи автоматически.</p><table><tr><th>ID</th><th>Автор</th><th>Контрагент</th><th>Обмен</th><th>Сумма</th><th>Статус</th><th>Группа</th><th>Управление</th></tr>{trs}</table></div>')


@app.post('/admin/deals/{did}/{action}')
async def review_deal(did:int, action:str, request:Request, csrf:str=Form()):
    from .policies import can_finish_deal
    check_csrf(request,csrf)
    if action not in ('complete', 'dispute'):
        raise HTTPException(404)
    async with Sessions() as s:
        d = await s.get(Deal,did)
        if not d or not can_finish_deal(d.status):
            raise HTTPException(409,'Сделка недоступна для изменения статуса')
        d.status = 'completed' if action=='complete' else 'disputed'
        s.add(Audit(actor_id=0,action='web_deal_status',detail=f'{did}:{d.status}'))
        await s.commit()
    return redirect('/admin/deals')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('fa_base.server:app',host='0.0.0.0',port=int(os.getenv('PORT','8080')))