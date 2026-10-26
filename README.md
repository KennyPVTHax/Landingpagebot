# Landingpagebot — Owner-template-first workflow (v3)

Private APK project previews controlled through Telegram. This is an **independent developer demonstration system**, not Google Play or a government service.

## Workflow

1. Owner issues an access key (`/issuekey`); developer uses `/redeem KEY`.
2. Bot displays **only templates uploaded and registered by the owner**. None are bundled.
3. Developer taps a template inline button. The bot reserves one slot and asks for an APK document.
4. Developer sends a self-produced APK (currently <=20 MB through hosted Telegram Bot API).
5. Backend automatically verifies APK ZIP structure, Android signature, package name and signer fingerprint using `apksigner` and `aapt`. **No manual human malware scan/review and no manual owner approval**.
6. If verification and per-user quota checks pass, APK is stored privately, linked to the slot, and a new unique review URL is returned. Invalid APKs get no usable link.
7. The owner's template renders and its `data-apk-install` link/button downloads that APK. All pages display an independent-developer notice and respect review-link revocation and account expiry.

## Owner-provided templates

This distribution contains **zero sample templates**. Do not use unauthorised, imitative, or third-party templates. You decide what designs are registered. See `owner_templates/README.md` for instructions. Provide your own built HTML bundle with relative paths and one annotated Install button:

```html
<a data-apk-install aria-disabled="true">Install APK</a>
```

For the title and metadata, optionally use `data-release-title`, `data-release-package`, and `data-release-version`. The backend loads only IDs in `owner_templates/registry.json` that have a matching `owner_templates/<id>/index.html`. Arbitrary users cannot upload templates. You can swap a template without changing the project link.

The project links use bearer tokens. Do not send them to anyone who is not meant to have download access.

## Owner quota controls (Telegram private chat)

| Command | Behavior |
|---|---|
| `/issuekey DAYS SLOTS [USES]` | Create expiring redeem key |
| `/setquota TELEGRAM_ID SLOTS APKS STORAGE_MB` | Replace one user's quotas |
| `/setallquota SLOTS APKS STORAGE_MB` | Replace quotas for existing users |
| `/setdefaults SLOTS APKS STORAGE_MB` | Set baseline for new access redemptions |
| `/grant TELEGRAM_ID DAYS SLOTS` | Grant/extend access |
| `/setexpiry TELEGRAM_ID DAYS` | Set expiry from now |
| `/suspend ID` / `/unsuspend ID` | Disable/restore account |
| `/users` / `/defaults` | View settings |
| `/setdomain https://host.example` | Set external link base, once DNS/TLS exist |
| `/revoke RELEASE_ID` | Emergency disable an APK release |

Regular users have `/start`, `/redeem`, `/templates`, `/status`, `/slots`, `/upload SLOT_ID`, `/apks`, `/rotate SLOT_ID`, `/disable SLOT_ID` and `/template SLOT_ID OWNER_TEMPLATE_ID`. The normal journey uses buttons, not typed IDs.

## Local setup (Ubuntu/Hostinger VPS)

Requires Python 3.11+, Android SDK Build Tools for `apksigner` and `aapt`, and a Telegram bot token from BotFather. Keep **all tokens and secrets** off GitHub. For first local tests, SQLite is fine; before public access switch to PostgreSQL and add migrations.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Generate **two different** secrets for `INTERNAL_API_TOKEN` and `KEY_PEPPER`, set `BOT_TOKEN` and `ADMIN_TELEGRAM_IDS` in `.env`. `APKSIGNER_PATH` and `AAPT_PATH` must resolve to installed Android SDK tools. `OWNER_TEMPLATE_DIR` is `./owner_templates` by default. `PUBLIC_BASE_URL` is optional and can be configured by the owner later.

In separate terminals:

```bash
uvicorn release_manager.main:app --host 127.0.0.1 --port 8087
python -m release_manager.bot
```

Run tests before connecting production Telegram account:

```bash
python -m pytest -q
python -m compileall -q release_manager
```

`/setdomain` **only changes link generation**; it does not establish DNS, install certificates or deploy Nginx. Links are relative until the domain is set, and are unusable outside the machine until deployment is configured.

## Security boundaries / limitations

- **No manual APK inspection**. Automatic APK structure and Android signature checks remain mandatory and fail closed. A valid APK signature does not guarantee the APK is harmless. Only allow developers distributing apps they are authorized to distribute.
- The `/internal/*` API is **bot-internal only**. Bind API to loopback and exclude these routes from any public proxy. It relies on an internal bearer secret and bot-authenticated Telegram IDs.
- Release files are stored outside the public web root under restrictive permissions and delivered only through revocable project links. No cloud storage integration yet.
- Standard Telegram `getFile` limits bot downloads to **20 MB**; larger releases require a future authenticated web uploader or a local Bot API server.
- The bot temporarily stores template→slot upload state in memory for 10 minutes. If it restarts, use `/upload SLOT_ID` to resume.
- No malware scanner, template upload via bot, or template marketplace is included. Only the owner deploys template bundles.
- Existing SQLite databases from prior prototypes need a migration or recreation; `create_all` does **not** migrate old columns. Add Alembic and PostgreSQL before live hosting.
- Review URLs are unguessable bearer links but are not substitutes for identity-based authorization. They may be forwarded by recipients; restrict distribution and rotate/revoke when needed.
- **Do not expose a government-app/Play-Store impersonation page distributing unrelated APKs.** Use templates you control with the independent developer notice and verified package information.

## Status

Local automated API tests verify access, owner-set quotas, template availability, APK failure-closed checks, unique-link issuance, download and revocation using a mocked Android signing result. Real device installation, Telegram BotFather integration, live Android SDK output and public Nginx hosting are not covered by these tests and must be validated before production.
