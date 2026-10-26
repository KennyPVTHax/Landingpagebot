#!/usr/bin/env python3
"""Owner message editor and local-only APK development mode for Landingpagebot."""
from pathlib import Path
from secrets import token_urlsafe
import json, shutil, time, os, py_compile

root = Path("/root/Landingpagebot")
bot, web, settings = (root / "release_manager" / f for f in ("bot.py","web.py","settings.py"))
for p in (bot, web, settings):
    if not p.exists(): raise SystemExit(f"Missing {p}")
stamp=time.strftime("%Y%m%d-%H%M%S")
for p in (bot,web,settings):
    shutil.copy2(p,str(p)+"."+stamp+".bak")

(root / "release_manager/message_design.py").write_text(r'''import html,json,os
from pathlib import Path
from contextvars import ContextVar
from aiogram import BaseMiddleware
from aiogram.types import Message,CallbackQuery
from aiogram.exceptions import TelegramBadRequest
STORE=Path(__file__).resolve().parent.parent / ".owner_messages.json"
CURRENT=ContextVar("bot_message_key", default="other")
LABELS={
"start":"WELCOME","help":"HELP","redeem":"ACCESS ACTIVATION",
"status":"ACCOUNT STATUS","templates":"AVAILABLE TEMPLATES",
"newslot":"NEW SLOT","choose":"TEMPLATE SELECTED",
"slots":"YOUR SLOTS","template":"TEMPLATE UPDATED",
"rotate":"LINK ROTATION","disable":"SLOT DISABLED",
"upload":"APK UPLOAD","apk_uploaded":"APK UPLOAD RESULT",
"apks":"APK RELEASES","adminhelp":"OWNER COMMANDS",
"issuekey":"ACCESS KEY","grant":"ACCESS GRANTED",
"setquota":"USER QUOTAS","setallquota":"GLOBAL QUOTAS",
"setdefaults":"DEFAULT QUOTAS","defaults":"DEFAULT SETTINGS",
"users":"USERS","setexpiry":"EXPIRY UPDATED",
"suspend":"SUSPEND ACCOUNT","unsuspend":"RESTORE ACCOUNT",
"setdomain":"DOMAIN SETTINGS","revoke":"RELEASE REVOKED"}
def get_all():
    try:
        data=json.loads(STORE.read_text())
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError): return {}
def save_all(data):
    temp=STORE.with_suffix(".tmp")
    fd=os.open(str(temp),os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as out:
        json.dump(data,out,ensure_ascii=False,indent=2)
    os.replace(temp,STORE)
    STORE.chmod(0o600)
class CommandContext(BaseMiddleware):
    async def __call__(self,handler,event,data):
        if isinstance(event,CallbackQuery):
            key="choose" if (event.data or "").startswith("choose:") else "other"
        elif isinstance(event,Message) and event.text and event.text.startswith("/"):
            key=event.text.split()[0].split("@")[0].lstrip("/").lower()
        elif isinstance(event,Message) and event.document:
            key="apk_uploaded"
        else:
            key="other"
        t=CURRENT.set(key)
        try: return await handler(event,data)
        finally: CURRENT.reset(t)
def render(key,original,override=None):
    if override is None: override=get_all().get(key)
    if override:
        return override.replace("{{default}}",html.escape(str(original)))
    return "<b>"+html.escape(LABELS.get(key,"MESSAGE"))+"</b>\n\n"+html.escape(str(original))
async def themed_answer(message,text,**kwargs):
    key=CURRENT.get()
    custom=get_all().get(key)
    try:
        return await message.answer(render(key,text,custom),parse_mode="HTML",**kwargs)
    except TelegramBadRequest:
        if custom:
            return await message.answer(render(key,text,""),parse_mode="HTML",**kwargs)
        raise
''')

s=bot.read_text()
old="from .settings import Settings\n"
new="from .settings import Settings\nfrom .message_design import CommandContext,LABELS,get_all,save_all,render,themed_answer\n"
if old in s and "from .message_design import" not in s: s=s.replace(old,new)
s=s.replace("await q.message.answer(","await themed_answer(q.message, ")
s=s.replace("await m.answer(","await themed_answer(m, ")
s=s.replace("allowed_updates=['message']","allowed_updates=['message', 'callback_query']")
if "dp.message.middleware(CommandContext())" not in s:
    s=s.replace("    dp.include_router(router)\n",
      "    dp.message.middleware(CommandContext())\n"
      "    dp.callback_query.middleware(CommandContext())\n"
      "    dp.include_router(router)\n")
old="""        await themed_answer(m, 'Active: ' + str(d['active']) + '\nSuspended: ' + str(d['suspended'])
                       + '\nQuotas: ' + str(d['quota']) + '\nUsed: ' + str(d['usage'])
                       + '\nExpiry (Unix UTC): ' + str(d['expires_at']))"""
new="""        from datetime import datetime, timezone
        expiry=datetime.fromtimestamp(d['expires_at'],timezone.utc).strftime('%d %b %Y, %H:%M UTC')
        q,u=d['quota'],d['usage']
        await themed_answer(m,
            ('✅ Active' if d['active'] else '⛔ Inactive') +
            (' • ⛔ Suspended' if d['suspended'] else ' • 🟢 Not suspended') +
            f'\n\n📦 Slots: {u["slots"]} / {q["slots"]}' +
            f'\n📱 APKs: {u["apks"]} / {q["apks"]}' +
            f'\n💾 Storage: {u["storage_bytes"]/(1024*1024):.2f} / {q["storage_mb"]} MB' +
            f'\n\n📅 Expires: {expiry}')"""
if old in s: s=s.replace(old,new)
editor=r'''
@router.message(Command('textmenu'))
async def textmenu(m: Message):
    if not owner(m): return
    custom=get_all()
    lines=['<b>🎨 OWNER MESSAGE EDITOR</b>',
           'Write a Telegram-formatted sample message, then REPLY to it with:',
           '<code>/settext status</code>',
           'Put <code>{{default}}</code> where live command data belongs.',
           '', '<b>Editable sections:</b>']
    lines += [('✅ ' if k in custom else '▫️ ')+'<code>'+k+'</code>' for k in LABELS]
    lines += ['', '<code>/previewtext status</code> — preview',
              '<code>/resettext status</code> — restore default']
    await m.answer('\n'.join(lines),parse_mode='HTML')

@router.message(Command('settext'))
async def settext(m: Message,command: CommandObject):
    if not owner(m): return
    key=(command.args or '').strip().lower().lstrip('/')
    if key not in LABELS:
        await m.answer('Unknown section; use /textmenu')
        return
    sample=m.reply_to_message
    if not sample or not sample.text:
        await m.answer('Write a formatted text and REPLY to it with /settext '+key)
        return
    content=sample.html_text
    if len(content)>3500:
        await m.answer('Maximum formatted text length is 3500 characters')
        return
    try:
        await m.answer('<b>Preview:</b>\n\n'+render(key,'Sample live command data',content),parse_mode='HTML')
    except Exception:
        await m.answer('Telegram rejected the format or Premium emoji. Nothing saved.')
        return
    all_=get_all()
    all_[key]=content
    save_all(all_)
    await m.answer('✅ Saved for /'+key+'; now active.')

@router.message(Command('previewtext'))
async def previewtext(m: Message,command: CommandObject):
    if not owner(m): return
    key=(command.args or '').strip().lower().lstrip('/')
    if key not in LABELS:
        await m.answer('Usage: /previewtext status')
        return
    await m.answer(render(key,'Sample live command data'),parse_mode='HTML')

@router.message(Command('resettext'))
async def resettext(m: Message,command: CommandObject):
    if not owner(m): return
    key=(command.args or '').strip().lower().lstrip('/')
    if key not in LABELS:
        await m.answer('Usage: /resettext status')
        return
    data=get_all()
    data.pop(key,None)
    save_all(data)
    await m.answer('Default formatting restored for /'+key)
'''
if "Command('textmenu')" not in s: s=s.replace("\nasync def main():","\n"+editor+"\nasync def main():")
bot.write_text(s)

s=settings.read_text()
if 'dev_allow_unverified_apk' not in s:
    s=s.replace("    aapt_path: str = 'aapt'\n","    aapt_path: str = 'aapt'\n    dev_allow_unverified_apk: bool = False  # local test only\n")
settings.write_text(s)
s=web.read_text()
s=s.replace("from .apk import InvalidApk, digest_file, verified_metadata",
            "from .apk import InvalidApk, digest_file, verified_metadata, structural_check")
old="                metadata = verified_metadata(tmp, config.apksigner_path, config.aapt_path)"
new="""                if config.dev_allow_unverified_apk:
                    with sessions() as ds:
                        domain=ds.get(Defaults,1).public_base_url or ''
                        release_slot=ds.get(Slot,slot_id)
                    if config.public_base_url or domain:
                        raise InvalidApk('Disable dev mode before enabling a public domain')
                    if not release_slot or release_slot.template_id != 'dev-test':
                        raise InvalidApk('Unverified APKs allowed only with dev-test template')
                    structural_check(tmp)
                    metadata={'package_name':'dev.local.unverified',
                              'version_name':'local-test','signer_sha256':'UNVERIFIED'}
                else:
                    metadata = verified_metadata(tmp, config.apksigner_path, config.aapt_path)"""
if old in s: s=s.replace(old,new)
elif 'dev.local.unverified' not in s: raise SystemExit('Verification code changed; no patch applied')
guard="""rel.signer_sha256 == 'UNVERIFIED' and (
                not config.dev_allow_unverified_apk or config.public_base_url or
                s.get(Defaults, 1).public_base_url or slot.template_id != 'dev-test')"""
if "if rel and "+guard not in s:
    s=s.replace(
        "        if rel and rel.state != 'approved':\n            rel = None",
        "        if rel and rel.state != 'approved':\n            rel = None\n"
        "        if rel and "+guard+":\n            rel = None")
if "        if "+guard not in s:
    s=s.replace(
        "        if not rel or rel.state != 'approved':\n            raise HTTPException(404, 'No approved APK available')",
        "        if not rel or rel.state != 'approved':\n            raise HTTPException(404, 'No approved APK available')\n"
        "        if "+guard+":\n"
        "            raise HTTPException(404, 'Development-only APK download disabled')")
web.write_text(s)

env=root/".env"
if not env.is_file(): raise SystemExit('Missing .env; source patched, test flag not enabled')
lines=env.read_text().splitlines()
lines=[x for x in lines if not x.startswith("DEV_ALLOW_UNVERIFIED_APK=")]
lines.append("DEV_ALLOW_UNVERIFIED_APK=true")
env.write_text("\n".join(lines)+"\n")
env.chmod(0o600)

demo=root/"owner_templates/dev-test/index.html"
demo.parent.mkdir(exist_ok=True,parents=True)
demo.write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Independent Developer Test</title><style>body{background:#111827;color:white;font:16px system-ui;padding:8vw;max-width:640px;margin:auto}a{background:#6479f9;color:white;padding:15px 24px;border-radius:10px;display:inline-block;text-decoration:none}p{color:#bdc5d3;line-height:1.7}</style><h1>🧪 Independent Developer APK Test</h1><p>Local testing only. This is NOT Google Play or a government website. This APK has NOT been signature verified. Only test apps you created or trust.</p><p>Package: <span data-release-package>Development test</span></p><a data-apk-install aria-disabled="true">Download local test APK</a></html>''')
registry=root/"owner_templates/registry.json"
data=json.loads(registry.read_text())
data=[x for x in data if x["id"]!="dev-test"]
data.append({"id":"dev-test","label":"Local APK Test (unverified)"})
registry.write_text(json.dumps(data,indent=2)+"\n")

p=root/".gitignore"
lines=p.read_text().splitlines() if p.exists() else []
for x in (".owner_messages.json","*.bak",".private_uploads/"):
    if x not in lines: lines.append(x)
p.write_text("\n".join(lines)+"\n")
for p in (bot,web,settings,root/"release_manager/message_design.py"):
    py_compile.compile(str(p),doraise=True)
print("✅ Owner message editor installed; /textmenu in Telegram")
print("✅ Local dev-test APK upload enabled, normal signed verification preserved")
print("✅ Unverified APKs blocked again when dev mode is disabled or a domain is set")
print("Restart the Landingpagebot API and bot processes (only these two).")