# Landingpagebot — Phase 1 skeleton

Independent Telegram-managed private release platform. **No APK distribution, templates, or domain routing is enabled yet.**

## Implemented
- Telegram private-chat command bot using aiogram
- FastAPI internal management API
- SQLAlchemy users, hashed redeem keys, redemptions and project slots
- Admin key creation, account grant/suspension, quotas and expiry
- Private, token-gated neutral review placeholders
- PostgreSQL-ready models (SQLite for local development only)

## Setup
Requires Python 3.11+, BotFather token, and numeric Telegram administrator ID.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate **two different** random secrets and set `INTERNAL_API_TOKEN` and `KEY_PEPPER` in your local `.env`. Set `BOT_TOKEN` and `ADMIN_TELEGRAM_IDS`. Never commit `.env`.

Start the API (terminal 1):
```bash
uvicorn release_manager.main:app --host 127.0.0.1 --port 8087
```
Start bot (terminal 2):
```bash
python -m release_manager.bot
```
Commands:
`/start`, `/redeem KEY`, `/status`, `/newslot TITLE`, `/slots`, `/disable ID`.
Admin: `/issuekey DAYS SLOTS [USES]`, `/grant USER_ID DAYS SLOTS`, `/suspend USER_ID`, `/unsuspend USER_ID`.

Run tests:
```bash
python -m pytest -q
python -m compileall -q release_manager
```

## Boundaries
The `/internal/*` routes are only for the bot and **must not be exposed publicly**. Review links are bearer credentials. SQLite and automatic `create_all` are development conveniences, not suitable for internet-facing production. Add Alembic migrations, proper audits, rate limits, transaction tests and PostgreSQL before deployment.

APKs built with Codex still require basic file integrity and signing checks when upload support is added. Malware scanning is deferred per project scope, but uploads and downloads must not be enabled before security controls are in place.

## Next
1. Complete tests and hardening; add audit log and key revocation.
2. Add private APK storage, metadata and signer verification, publisher approval, release records.
3. Add original, independently branded landing-page templates and template selection.
4. Add your domain, HTTPS, protected download links and VPS deployment.

This repository does not host or impersonate Google Play or any government service.
