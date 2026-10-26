#!/usr/bin/env python3
"""Time-limited private HTTPS APK integration testing.

Requires the prior scripts/patch_owner_messages_local_apk_test.py changes.
Unverified APKs: owner-only upload, dev-test template only, explicit test domain,
expiry, nginx HTTP Basic authentication, and automatic download disable.
"""
from pathlib import Path
from datetime import datetime, timezone
import json, os, shutil, subprocess, secrets, time, py_compile

root=Path("/root/Landingpagebot")
web=root/"release_manager/web.py"
settings=root/"release_manager/settings.py"
bot=root/"release_manager/bot.py"
env=root/".env"
nginx=Path("/etc/nginx/sites-available/testingforpagekenny.com")
if os.geteuid()!=0: raise SystemExit("Run as root")
for p in (web,settings,bot,env,nginx):
    if not p.exists(): raise SystemExit(f"Required file missing: {p}")

s=web.read_text()
if "raise InvalidApk('Disable dev mode before enabling a public domain')" not in s and "def dev_preview_allowed" not in s:
    raise SystemExit("Expected existing development-gate patch not found. No changes made.")
if "from .apk import InvalidApk, digest_file, verified_metadata, structural_check" not in s:
    raise SystemExit("Expected structural-check import missing. No changes made.")
n=nginx.read_text()
marker="location ^~ /p/ {"
if marker not in n and "landingpagebot-test.htpasswd" not in n:
    raise SystemExit("Expected /p/ Nginx location missing. No changes made.")
if "server_name testingforpagekenny.com" not in n:
    raise SystemExit("Unexpected Nginx hostname. No changes made.")

stamp=str(int(time.time()))
for p in (web,settings,bot,env,nginx):
    target=str(p)+"."+stamp+".pretest"
    shutil.copy2(p,target)
print("Backups saved with extension:",stamp+".pretest")

s=settings.read_text()
if "dev_unverified_expires_at" not in s:
    needle="    dev_allow_unverified_apk: bool = False"
    if needle not in s: raise SystemExit("Missing dev-mode setting in settings.py")
    s=s.replace(needle,needle+"\n    dev_unverified_expires_at: int = 0")
settings.write_text(s)

s=web.read_text()
if "def dev_preview_allowed" not in s:
    marker="    @app.post('/internal/apk/upload', dependencies=protected)"
    helper="""    def dev_preview_allowed(s, slot, rel=None):
        if not config.dev_allow_unverified_apk or now() >= config.dev_unverified_expires_at:
            return False
        if not slot or slot.template_id != 'dev-test':
            return False
        owner = s.get(User, slot.owner_id)
        if not owner or owner.telegram_id not in config.admins:
            return False
        domain = s.get(Defaults, 1).public_base_url or config.public_base_url
        return domain.rstrip('/') == 'https://testingforpagekenny.com'

"""
    if marker not in s: raise SystemExit("Upload endpoint not found")
    s=s.replace(marker,helper+marker)
old="""                    if config.public_base_url or domain:
                        raise InvalidApk('Disable dev mode before enabling a public domain')"""
new="""                    if telegram_id not in config.admins:
                        raise InvalidApk('Development APK uploads are restricted to the owner')
                    if now() >= config.dev_unverified_expires_at:
                        raise InvalidApk('Development APK upload window expired')
                    effective_domain=(domain or config.public_base_url).rstrip('/')
                    if effective_domain != 'https://testingforpagekenny.com':
                        raise InvalidApk('Development APK uploads require the configured test domain')"""
if old in s: s=s.replace(old,new)
elif "Development APK uploads are restricted to the owner" not in s:
    raise SystemExit("Development mode upload guard differed from expected")
# Replace the two existing hide/reject guards, using anchored snippets.
old="""rel.signer_sha256 == 'UNVERIFIED' and (
                not config.dev_allow_unverified_apk or config.public_base_url or
                s.get(Defaults, 1).public_base_url or slot.template_id != 'dev-test')"""
new="rel.signer_sha256 == 'UNVERIFIED' and not dev_preview_allowed(s, slot, rel)"
if old in s: s=s.replace(old,new)
elif new not in s: raise SystemExit("Expected unverified APK download guard missing")
# Automatically reject unverified uploads after the expiry or on a different site.
web.write_text(s)

s=bot.read_text()
s=s.replace(
    "'The Install button now downloads this signed APK.'",
    "('TEST DOWNLOAD: APK signature NOT verified.' if d.get('signer_sha256') == 'UNVERIFIED' else 'Verified signed APK download available.')"
)
bot.write_text(s)

values={}
for line in env.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key,value=line.split("=",1)
        values[key]=value
values["DEV_ALLOW_UNVERIFIED_APK"]="true"
values["PUBLIC_BASE_URL"]="https://testingforpagekenny.com"
values["DEV_UNVERIFIED_EXPIRES_AT"]=str(int(time.time())+21600) # 6 hours
if "8542817214" not in values.get("ADMIN_TELEGRAM_IDS","").split(","):
    raise SystemExit("Owner Telegram ID missing from .env; refusing to enable dev mode")
old_lines=env.read_text().splitlines()
old_lines=[line for line in old_lines if line.partition("=")[0] not in ("DEV_ALLOW_UNVERIFIED_APK","PUBLIC_BASE_URL","DEV_UNVERIFIED_EXPIRES_AT")]
old_lines.extend(f"{k}={values[k]}" for k in ("DEV_ALLOW_UNVERIFIED_APK","PUBLIC_BASE_URL","DEV_UNVERIFIED_EXPIRES_AT"))
env.write_text("\n".join(old_lines)+"\n")
env.chmod(0o600)

demo=root/"owner_templates/dev-test/index.html"
demo.parent.mkdir(exist_ok=True,parents=True)
demo.write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Private Developer APK Test</title><style>body{background:#101827;color:#fff;font:16px system-ui;line-height:1.6;padding:6vw;max-width:640px;margin:auto}a{display:inline-block;background:#6481ee;color:#fff;padding:14px 20px;border-radius:10px;text-decoration:none}section{background:#2e2733;border:1px solid #df926d;border-radius:12px;padding:16px}small{color:#bac7d4}</style></head><body><h1>🧪 Private Developer APK Test</h1><section><strong>UNVERIFIED APK — TEST ONLY</strong><p>This is a password-protected independent developer test. The APK has NOT been signature-verified. This is not Google Play or a government service. Do not install unless you created and trust the file.</p></section><p>Package: <span data-release-package>Development test</span></p><p>Version: <span data-release-version>Test build</span></p><a data-apk-install aria-disabled="true">Download unverified test APK</a><p><small>This testing exception expires automatically. Do not distribute this private preview link.</small></p></body></html>''')
reg=root/"owner_templates/registry.json"
templates=json.loads(reg.read_text())
templates=[t for t in templates if t.get("id") not in ("dev-test","template-one")]
templates.append({"id":"dev-test","label":"Private Developer APK Test — UNVERIFIED"})
reg.write_text(json.dumps(templates,indent=2)+"\n")

for p in (web,settings,bot):
    py_compile.compile(str(p),doraise=True)

# Protect public release URLs from general internet access.
creds=Path("/etc/nginx/landingpagebot-test.htpasswd")
if creds.exists():
    print("Reusing existing test HTTP Basic authentication credentials.")
else:
    password=secrets.token_urlsafe(15)
    result=subprocess.run(["openssl","passwd","-apr1","-stdin"],
                          input=password+"\n",capture_output=True,text=True,check=True)
    creds.write_text("apkpreview:"+result.stdout.strip()+"\n")
    creds.chmod(0o644)  # nginx workers must be able to read the hash file
    pwfile=root/".test-access-password"
    pwfile.write_text("Username: apkpreview\nPassword: "+password+"\n")
    pwfile.chmod(0o600)
    print("Private website test password stored in /root/Landingpagebot/.test-access-password")

n=nginx.read_text()
if "landingpagebot-test.htpasswd" not in n:
    n=n.replace("location ^~ /p/ {",
        'location ^~ /p/ {\n        auth_basic "Private developer APK preview";\n'
        "        auth_basic_user_file /etc/nginx/landingpagebot-test.htpasswd;")
    nginx.write_text(n)
    test=subprocess.run(["nginx","-t"],capture_output=True,text=True)
    if test.returncode:
        shutil.copy2(str(nginx)+"."+stamp+".pretest",nginx)
        raise SystemExit("Nginx syntax failure; original Nginx file restored:\n"+test.stderr)
    subprocess.run(["systemctl","reload","nginx"],check=True)
    print("Nginx preview passwords enabled on /p/.")

print("READY: Owner-only unverified APK upload for dev-test on testingforpagekenny.com")
print("EXPIRES UTC:",datetime.fromtimestamp(int(values["DEV_UNVERIFIED_EXPIRES_AT"]),timezone.utc).isoformat())
print("Restart ONLY the Landingpagebot API and Telegram bot processes.")
