"""Private-chat-only Telegram command interface for the internal API."""
import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .settings import Settings

router = Router()
settings: Settings | None = None

def uid(m: Message):
    if m.chat.type != "private" or not m.from_user or m.from_user.is_bot:
        return None
    return m.from_user.id

async def api(endpoint, body):
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=15) as client:
        r = await client.post("/internal/" + endpoint, json=body,
                              headers={"X-Internal-Token": settings.internal_api_token})
        try:
            value = r.json()
        except ValueError:
            raise RuntimeError("Backend returned invalid response")
        if r.status_code >= 400:
            raise RuntimeError(value.get("detail", "Request failed"))
        return value

async def invoke(m, endpoint, body):
    try:
        return await api(endpoint, body)
    except (RuntimeError, httpx.HTTPError) as exc:
        await m.answer("Request failed: " + str(exc))
        return None

@router.message(Command("start", "help"))
async def start(m: Message):
    if uid(m) is None: return
    await m.answer("Independent Release Manager (skeleton)\n"
                   "/redeem KEY — activate\n/status — access and quota\n"
                   "/newslot TITLE — reserve a project slot\n"
                   "/slots — list slots\n/disable ID — disable a slot\n"
                   "/adminhelp — owner commands\n"
                   "Templates, domains, APK uploads and downloads are not enabled.")

@router.message(Command("redeem"))
async def redeem(m: Message, command: CommandObject):
    if uid(m) is None: return
    key = (command.args or "").strip()
    if not key:
        await m.answer("Usage: /redeem KEY")
        return
    try:
        await m.delete()
    except Exception:
        pass
    data = await invoke(m, "redeem", {"telegram_id": uid(m), "key": key})
    if data: await m.answer("Access active. Slot quota: " + str(data["quota"]))

@router.message(Command("status"))
async def status(m: Message):
    if uid(m) is None: return
    data = await invoke(m, "status", {"telegram_id": uid(m)})
    if data:
        await m.answer(f'Active: {data["active"]}\n'
                       f'Suspended: {data["suspended"]}\n'
                       f'Slots: {data["slots"]}/{data["quota"]}\n'
                       f'Access expiry (Unix UTC): {data["expires_at"]}')

@router.message(Command("newslot"))
async def newslot(m: Message, command: CommandObject):
    if uid(m) is None: return
    title = (command.args or "").strip()
    if len(title) < 2:
        await m.answer("Usage: /newslot My App")
        return
    data = await invoke(m, "slots", {"telegram_id": uid(m), "title": title})
    if data:
        await m.answer(f'Slot #{data["id"]} created.\nPrivate path (domain not configured):\n'
                       + data["path"] + "\nKeep this link secret. No APK is available yet.")

@router.message(Command("slots"))
async def slots(m: Message):
    if uid(m) is None: return
    data = await invoke(m, "slots/list", {"telegram_id": uid(m)})
    if data:
        await m.answer("\n".join(f'#{x["id"]} {x["title"]} — {"enabled" if x["enabled"] else "disabled"}'
                                  for x in data["slots"]) or "No slots created.")

@router.message(Command("disable"))
async def disable(m: Message, command: CommandObject):
    if uid(m) is None: return
    try:
        slot_id = int((command.args or "").strip())
    except ValueError:
        await m.answer("Usage: /disable SLOT_ID")
        return
    data = await invoke(m, "slots/disable", {"telegram_id": uid(m), "slot_id": slot_id})
    if data: await m.answer("Slot disabled.")

@router.message(Command("adminhelp"))
async def adminhelp(m: Message):
    if uid(m) not in settings.admins: return
    await m.answer("/issuekey DAYS SLOTS [USES]\n"
                   "/grant TELEGRAM_ID DAYS SLOTS\n"
                   "/suspend TELEGRAM_ID\n/unsuspend TELEGRAM_ID")

@router.message(Command("issuekey"))
async def issuekey(m: Message, command: CommandObject):
    if uid(m) not in settings.admins: return
    try:
        args = [int(x) for x in (command.args or "").split()]
        if len(args) not in (2, 3): raise ValueError()
    except ValueError:
        await m.answer("Usage: /issuekey DAYS SLOTS [USES]")
        return
    data = await invoke(m, "issue", {"telegram_id": uid(m), "days": args[0],
                                    "slots": args[1], "uses": args[2] if len(args) > 2 else 1})
    if data: await m.answer("Redeem key (shown once):\n" + data["key"])

@router.message(Command("grant"))
async def grant(m: Message, command: CommandObject):
    if uid(m) not in settings.admins: return
    try:
        target, days, slots = map(int, (command.args or "").split())
    except ValueError:
        await m.answer("Usage: /grant TELEGRAM_ID DAYS SLOTS")
        return
    data = await invoke(m, "grant", {"telegram_id": uid(m), "target_id": target,
                                    "days": days, "slots": slots})
    if data: await m.answer("Access granted.")

@router.message(Command("suspend", "unsuspend"))
async def suspend(m: Message, command: CommandObject):
    if uid(m) not in settings.admins: return
    try:
        target = int((command.args or "").strip())
    except ValueError:
        await m.answer("Usage: /suspend TELEGRAM_ID or /unsuspend TELEGRAM_ID")
        return
    is_suspended = m.text.split()[0].split("@")[0].lstrip("/") == "suspend"
    data = await invoke(m, "suspend", {"telegram_id": uid(m), "target_id": target,
                                      "value": is_suspended})
    if data: await m.answer("Account updated.")

async def main():
    global settings
    settings = Settings()
    if not settings.bot_token or not settings.admins:
        raise SystemExit("Configure BOT_TOKEN and ADMIN_TELEGRAM_IDS")
    logging.basicConfig(level=logging.WARNING)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
