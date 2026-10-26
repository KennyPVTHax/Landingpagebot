"""Telegram private-chat management, no web-facing bot token or source secrets."""
import asyncio
import io
import logging
import time

import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from .settings import Settings

router = Router()
settings: Settings | None = None
pending_uploads: dict[int, tuple[int, float]] = {}


def uid(m: Message) -> int | None:
    if m.chat.type != 'private' or not m.from_user or m.from_user.is_bot:
        return None
    return m.from_user.id


def owner(m: Message) -> bool:
    return uid(m) is not None and uid(m) in settings.admins


async def api(endpoint, body=None, files=None, data=None):
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=90) as client:
        r = await client.post('/internal/' + endpoint, json=body if files is None else None,
                              data=data, files=files,
                              headers={'X-Internal-Token': settings.internal_api_token})
        try:
            value = r.json()
        except ValueError as exc:
            raise RuntimeError(f'Backend returned HTTP {r.status_code}, not JSON') from exc
        if r.status_code >= 400:
            raise RuntimeError(str(value.get('detail', 'Request failed')))
        return value


async def invoke(m, endpoint, body=None, files=None, data=None):
    try:
        return await api(endpoint, body, files, data)
    except (RuntimeError, httpx.HTTPError) as exc:
        await m.answer('Request failed: ' + str(exc)[:500])
        return None


def ints(args, count, extra=False):
    try:
        values = list(map(int, (args or '').split()))
        if len(values) != count and not (extra and len(values) == count + 1):
            return None
        return values
    except ValueError:
        return None


@router.message(Command('start', 'help'))
async def help_command(m: Message):
    if uid(m) is None: return
    await m.answer('APK Release Manager — independent project previews\n'
                   '/redeem KEY • /status • /templates • /slots\n'
                   'Select your template first, then upload an APK.\n'
                   '/upload SLOT_ID (then send APK ≤20 MB)\n'
                   '/apks • /rotate SLOT_ID • /disable SLOT_ID\n'
                   '/adminhelp (owner only). Signed APKs auto-publish without manual review.')


@router.message(Command('redeem'))
async def redeem(m: Message, command: CommandObject):
    if uid(m) is None: return
    key = (command.args or '').strip()
    if not key:
        await m.answer('Usage: /redeem KEY')
        return
    try:
        await m.delete()
    except Exception:
        pass
    data = await invoke(m, 'redeem', {'telegram_id': uid(m), 'key': key})
    if data:
        await m.answer('Activated. Quotas: ' + str(data['quota']) + '\nChoose your landing page template:')
        await send_templates(m)


@router.message(Command('status'))
async def status(m: Message):
    if uid(m) is None: return
    d = await invoke(m, 'status', {'telegram_id': uid(m)})
    if d:
        await m.answer('Active: ' + str(d['active']) + '\nSuspended: ' + str(d['suspended'])
                       + '\nQuotas: ' + str(d['quota']) + '\nUsed: ' + str(d['usage'])
                       + '\nExpiry (Unix UTC): ' + str(d['expires_at']))


@router.message(Command('newslot'))
async def newslot(m: Message):
    if uid(m) is None: return
    await m.answer('Choose an owner-provided template to reserve a new slot:')
    await send_templates(m)


async def send_templates(m: Message):
    data = await invoke(m, 'templates', {'telegram_id': uid(m)})
    if not data:
        return
    if not data['templates']:
        await m.answer('No owner-supplied landing page templates are installed yet.')
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t['label'], callback_data='choose:' + t['id'])]
        for t in data['templates']])
    await m.answer('Select a template:', reply_markup=keyboard)


@router.callback_query(F.data.startswith('choose:'))
async def choose_template(q: CallbackQuery):
    if not q.from_user or q.from_user.is_bot or not q.message or q.message.chat.type != 'private':
        await q.answer('Private chat only', show_alert=True)
        return
    template_id = (q.data or '').split(':', 1)[1]
    await q.answer()
    data = await invoke(q.message, 'slots', {
        'telegram_id': q.from_user.id,
        'template_id': template_id,
        'title': 'Developer project',
    })
    if not data:
        return
    pending_uploads[q.from_user.id] = (data['id'], time.time() + 600)
    await q.message.answer('Template selected. Slot #' + str(data['id']) +
                           ' is reserved. Now send your APK as a Telegram document '
                           '(maximum ' + str(settings.max_upload_mb) + ' MB). '
                           'After automated file/signature checks, you will receive your link. '
                           'If you need to retry, use /upload ' + str(data['id']) + '.')


@router.message(Command('slots'))
async def slots(m: Message):
    if uid(m) is None: return
    d = await invoke(m, 'slots/list', {'telegram_id': uid(m)})
    if d:
        await m.answer('\n'.join(f'#{x["id"]} {x["title"]} ({x["template_id"]})'
                                 f' — {"enabled" if x["enabled"] else "disabled"}'
                                 for x in d['slots']) or 'No slots.')


@router.message(Command('templates'))
async def templates(m: Message):
    if uid(m) is None: return
    await send_templates(m)


@router.message(Command('template'))
async def template(m: Message, command: CommandObject):
    if uid(m) is None: return
    args = (command.args or '').split()
    if len(args) != 2 or not args[0].isdigit():
        await m.answer('Usage: /template SLOT_ID OWNER_TEMPLATE_ID')
        return
    d = await invoke(m, 'slots/template', {'telegram_id': uid(m), 'slot_id': int(args[0]),
                                          'template_id': args[1]})
    if d: await m.answer('Template changed to ' + d['template_id'])


@router.message(Command('rotate', 'disable'))
async def slot_action(m: Message, command: CommandObject):
    if uid(m) is None: return
    args = ints(command.args, 1)
    if not args:
        await m.answer('Usage: /rotate SLOT_ID or /disable SLOT_ID')
        return
    op = (m.text or '').split()[0].split('@')[0].lstrip('/')
    endpoint = 'slots/rotate' if op == 'rotate' else 'slots/disable'
    d = await invoke(m, endpoint, {'telegram_id': uid(m), 'slot_id': args[0]})
    if d:
        await m.answer('New private path (old link revoked):\n' + d['path'] if op == 'rotate'
                       else 'Slot disabled.')


@router.message(Command('upload'))
async def upload_command(m: Message, command: CommandObject):
    if uid(m) is None: return
    args = ints(command.args, 1)
    if not args or args[0] <= 0:
        await m.answer('Usage: /upload SLOT_ID')
        return
    pending_uploads[uid(m)] = (args[0], time.time() + 600)
    await m.answer('Send the APK as a Telegram document (not a photo). '
                   f'Maximum {settings.max_upload_mb} MB. Expires in 10 minutes. '
                   'After automated integrity/signature validation, the APK will be linked immediately.')


@router.message(F.document)
async def document(m: Message, bot: Bot):
    if uid(m) is None: return
    task = pending_uploads.pop(uid(m), None)
    if not task or task[1] < time.time():
        await m.answer('Use /upload SLOT_ID before sending an APK.')
        return
    doc = m.document
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if not (doc.file_name or '').lower().endswith('.apk') or doc.file_size is None or doc.file_size > max_bytes:
        await m.answer('Only .apk documents ≤20 MB are supported in this phase.')
        return
    try:
        stream = io.BytesIO()
        await bot.download(doc, destination=stream, timeout=60)
        if stream.tell() > max_bytes:
            await m.answer('APK exceeded size limit.')
            return
        stream.seek(0)
        d = await invoke(m, 'apk/upload', files={
            'document': (doc.file_name, stream, 'application/vnd.android.package-archive')},
            data={'telegram_id': str(uid(m)), 'slot_id': str(task[0])})
        if d:
            url = d.get('url') or d['path']
            await m.answer('Your APK is linked successfully!\n'
                           f'Project: {d["package_name"]}\n'
                           f'SHA-256: {d["sha256"]}\n'
                           f'Unique link: {url}\n'
                           + ('Domain not configured yet; this is a relative path.'
                              if not d.get('domain_configured') else
                              'The Install button now downloads this signed APK.'))
    except Exception as exc:
        logging.exception('Telegram APK transfer failed')
        await m.answer('Upload could not finish. Please try again.')


@router.message(Command('apks'))
async def apks(m: Message):
    if uid(m) is None: return
    d = await invoke(m, 'apk/list', {'telegram_id': uid(m)})
    if d: await m.answer('\n'.join(f'#{x["id"]} {x["filename"]} • {x["state"]} (slot {x["slot_id"]})'
                                  for x in d['releases']) or 'No APK releases.')


@router.message(Command('adminhelp'))
async def adminhelp(m: Message):
    if not owner(m): return
    await m.answer('OWNER COMMANDS:\n'
                   '/issuekey DAYS SLOTS [USES]\n/grant USER_ID DAYS SLOTS\n'
                   '/setquota USER_ID SLOTS APKS STORAGE_MB\n'
                   '/setallquota SLOTS APKS STORAGE_MB (existing users)\n'
                   '/setdefaults SLOTS APKS STORAGE_MB (new accounts)\n'
                   '/defaults • /users • /setexpiry USER_ID DAYS_FROM_NOW\n'
                   '/suspend USER_ID • /unsuspend USER_ID\n'
                   '/setdomain https://YOUR-DOMAIN (DNS configured later)\n'
                   '/revoke RELEASE_ID (emergency release revocation)')


@router.message(Command('issuekey'))
async def issuekey(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 2, extra=True)
    if not args:
        await m.answer('Usage: /issuekey DAYS SLOTS [USES]')
        return
    d = await invoke(m, 'issue', {'telegram_id': uid(m), 'days': args[0],
                                 'slots': args[1], 'uses': args[2] if len(args) == 3 else 1})
    if d: await m.answer('Redeem key (one-time display):\n' + d['key'])


@router.message(Command('grant'))
async def grant(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 3)
    if not args:
        await m.answer('Usage: /grant USER_ID DAYS SLOTS')
        return
    d = await invoke(m, 'grant', {'telegram_id': uid(m), 'target_id': args[0],
                                 'days': args[1], 'slots': args[2]})
    if d: await m.answer('Account granted or extended.')


@router.message(Command('setquota'))
async def setquota(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 4)
    if not args:
        await m.answer('Usage: /setquota USER_ID SLOTS APKS STORAGE_MB')
        return
    d = await invoke(m, 'setquota', {'telegram_id': uid(m), 'target_id': args[0],
                                    'slots': args[1], 'apks': args[2], 'storage_mb': args[3]})
    if d: await m.answer('User quota updated: ' + str(d['quota']))


@router.message(Command('setallquota', 'setdefaults'))
async def globalquota(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 3)
    if not args:
        await m.answer('Usage: /setdefaults SLOTS APKS STORAGE_MB or /setallquota SLOTS APKS STORAGE_MB')
        return
    operation = (m.text or '').split()[0].split('@')[0].lstrip('/')
    d = await invoke(m, operation, {'telegram_id': uid(m), 'slots': args[0],
                                    'apks': args[1], 'storage_mb': args[2]})
    if d: await m.answer('Settings updated: ' + str(d))


@router.message(Command('defaults'))
async def defaults(m: Message):
    if not owner(m): return
    d = await invoke(m, 'defaults', {'telegram_id': uid(m)})
    if d: await m.answer('New-account defaults: ' + str(d))


@router.message(Command('users'))
async def users(m: Message):
    if not owner(m): return
    d = await invoke(m, 'users', {'telegram_id': uid(m)})
    if d: await m.answer('\n'.join(f'{x["telegram_id"]} • quota {x["quota"]} • '
                                 f'{"suspended" if x["suspended"] else "enabled"}'
                                 for x in d['users'])[:3800] or 'No accounts.')


@router.message(Command('setexpiry'))
async def expiry(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 2)
    if not args:
        await m.answer('Usage: /setexpiry USER_ID DAYS_FROM_NOW')
        return
    d = await invoke(m, 'setexpiry', {'telegram_id': uid(m), 'target_id': args[0],
                                     'days_from_now': args[1]})
    if d: await m.answer('Access expiry updated.')


@router.message(Command('suspend', 'unsuspend'))
async def suspend(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 1)
    if not args:
        await m.answer('Usage: /suspend USER_ID or /unsuspend USER_ID')
        return
    suspend_account = (m.text or '').split()[0].split('@')[0].lstrip('/') == 'suspend'
    d = await invoke(m, 'suspend', {'telegram_id': uid(m), 'target_id': args[0],
                                   'value': suspend_account})
    if d: await m.answer('Account updated.')


@router.message(Command('setdomain'))
async def setdomain(m: Message, command: CommandObject):
    if not owner(m): return
    domain = (command.args or '').strip()
    if not domain:
        await m.answer('Usage: /setdomain https://releases.example.com')
        return
    d = await invoke(m, 'setdomain', {'telegram_id': uid(m), 'url': domain})
    if d:
        await m.answer('Domain saved. You must separately configure DNS, HTTPS, and '
                       'the reverse proxy before generated URLs are accessible.')


@router.message(Command('revoke'))
async def revoke(m: Message, command: CommandObject):
    if not owner(m): return
    args = ints(command.args, 1)
    if not args or args[0] < 1:
        await m.answer('Usage: /revoke RELEASE_ID')
        return
    d = await invoke(m, 'apk/revoke', {'telegram_id': uid(m), 'release_id': args[0]})
    if d: await m.answer('Release revoked; associated download is disabled.')


async def main():
    global settings
    settings = Settings()
    if not settings.bot_token or not settings.admins:
        raise SystemExit('Set BOT_TOKEN and ADMIN_TELEGRAM_IDS')
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=['message'])
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
