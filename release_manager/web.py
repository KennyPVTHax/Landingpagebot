"""Internal-only management API; template and APK delivery are future work."""
from contextlib import asynccontextmanager
from html import escape
import hmac
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, User, Slot, AccessKey, Redemption
from .security import now, token, keyed_hash
from .settings import Settings

class Identity(BaseModel):
    telegram_id: int = Field(gt=0)

class Redeem(Identity):
    key: str = Field(min_length=10, max_length=150)

class Issue(BaseModel):
    telegram_id: int
    days: int = Field(ge=1, le=365)
    slots: int = Field(ge=1, le=50)
    uses: int = Field(default=1, ge=1, le=100)

class Grant(BaseModel):
    telegram_id: int
    target_id: int
    days: int = Field(ge=1, le=365)
    slots: int = Field(ge=1, le=50)

class Suspend(BaseModel):
    telegram_id: int
    target_id: int
    value: bool

class NewSlot(Identity):
    title: str = Field(min_length=2, max_length=90)

class SlotAction(Identity):
    slot_id: int = Field(gt=0)

def create_app(config: Settings | None = None):
    config = config or Settings()
    sqlite = config.database_url.startswith("sqlite")
    engine = create_engine(config.database_url, pool_pre_ping=True,
                           connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:
        from sqlalchemy import event
        @event.listens_for(engine, "connect")
        def foreign_keys(conn, record):
            conn.execute("PRAGMA foreign_keys=ON")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifetime(app):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Release Manager Skeleton", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifetime)

    def db():
        with sessions() as s:
            yield s

    def internal(x_internal_token: str | None = Header(default=None)):
        if x_internal_token is None or not hmac.compare_digest(x_internal_token, config.internal_api_token):
            raise HTTPException(401, "Unauthorized")

    def admin(uid):
        if uid not in config.admins:
            raise HTTPException(403, "Admin only")

    def find_user(s, uid):
        return s.scalar(select(User).where(User.telegram_id == uid).with_for_update())

    def active(s, uid):
        u = find_user(s, uid)
        if not u or u.suspended or u.active_until <= now():
            raise HTTPException(403, "Inactive account")
        return u

    @app.get("/health")
    def health():
        return {"ok": True, "status": "skeleton"}

    @app.post("/internal/status", dependencies=[Depends(internal)])
    def status(p: Identity, s: Session = Depends(db)):
        u = find_user(s, p.telegram_id)
        count = s.scalar(select(func.count(Slot.id)).where(Slot.owner_id == u.id, Slot.enabled.is_(True))) if u else 0
        return {"admin": p.telegram_id in config.admins,
                "active": bool(u and not u.suspended and u.active_until > now()),
                "suspended": bool(u and u.suspended), "slots": count,
                "quota": u.quota if u else 0, "expires_at": u.active_until if u else 0}

    @app.post("/internal/issue", dependencies=[Depends(internal)])
    def issue(p: Issue, s: Session = Depends(db)):
        admin(p.telegram_id)
        raw = "RM-" + token()
        with s.begin():
            s.add(AccessKey(key_hash=keyed_hash(raw, config.key_pepper),
                            duration_days=p.days, quota=p.slots,
                            remaining=p.uses, expires_at=now() + 30 * 86400))
        return {"key": raw}

    @app.post("/internal/redeem", dependencies=[Depends(internal)])
    def redeem(p: Redeem, s: Session = Depends(db)):
        with s.begin():
            u = find_user(s, p.telegram_id)
            if u and u.suspended:
                raise HTTPException(403, "Account suspended")
            key_hash = keyed_hash(p.key.strip(), config.key_pepper)
            # Atomic claim; a failed/duplicate redemption rolls the transaction back.
            k = s.execute(update(AccessKey).where(
                AccessKey.key_hash == key_hash, AccessKey.remaining > 0,
                AccessKey.expires_at > now()).values(
                remaining=AccessKey.remaining - 1).returning(
                AccessKey.id, AccessKey.duration_days, AccessKey.quota)).first()
            if k is None:
                raise HTTPException(400, "Invalid or exhausted key")
            if u is None:
                u = User(telegram_id=p.telegram_id)
                s.add(u)
                s.flush()
            if s.scalar(select(Redemption.id).where(
                    Redemption.user_id == u.id, Redemption.key_id == k.id)):
                raise HTTPException(409, "Already redeemed")
            s.add(Redemption(user_id=u.id, key_id=k.id))
            u.active_until = max(now(), u.active_until) + k.duration_days * 86400
            u.quota = max(u.quota, k.quota)
            result = {"active": True, "quota": u.quota, "expires_at": u.active_until}
        return result

    @app.post("/internal/grant", dependencies=[Depends(internal)])
    def grant(p: Grant, s: Session = Depends(db)):
        admin(p.telegram_id)
        with s.begin():
            u = find_user(s, p.target_id)
            if u is None:
                u = User(telegram_id=p.target_id)
                s.add(u)
            u.active_until = max(now(), u.active_until) + p.days * 86400
            u.quota = max(u.quota, p.slots)
            u.suspended = False
        return {"ok": True}

    @app.post("/internal/suspend", dependencies=[Depends(internal)])
    def suspend(p: Suspend, s: Session = Depends(db)):
        admin(p.telegram_id)
        with s.begin():
            u = find_user(s, p.target_id)
            if not u:
                raise HTTPException(404, "Unknown user")
            u.suspended = p.value
        return {"ok": True}

    @app.post("/internal/slots", dependencies=[Depends(internal)])
    def newslot(p: NewSlot, s: Session = Depends(db)):
        with s.begin():
            u = active(s, p.telegram_id)
            count = s.scalar(select(func.count(Slot.id)).where(
                Slot.owner_id == u.id, Slot.enabled.is_(True)))
            if count >= u.quota:
                raise HTTPException(403, "Slot quota reached")
            secret = token()
            slug = secrets.token_urlsafe(12)
            slot = Slot(owner_id=u.id, slug=slug, title=p.title.strip(),
                        review_token_hash=keyed_hash(secret, config.key_pepper),
                        enabled=True)
            s.add(slot)
            s.flush()
            result = {"id": slot.id, "slug": slug, "token": secret}
        # No domain selected yet. API returns relative link only.
        result["path"] = f"/p/{slug}?t={secret}"
        return result

    @app.post("/internal/slots/list", dependencies=[Depends(internal)])
    def listslots(p: Identity, s: Session = Depends(db)):
        u = find_user(s, p.telegram_id)
        if u is None:
            return {"slots": []}
        slots = s.scalars(select(Slot).where(Slot.owner_id == u.id).order_by(Slot.id)).all()
        return {"slots": [{"id": x.id, "title": x.title, "slug": x.slug,
                           "enabled": x.enabled, "template_id": x.template_id} for x in slots]}

    @app.post("/internal/slots/disable", dependencies=[Depends(internal)])
    def disable(p: SlotAction, s: Session = Depends(db)):
        with s.begin():
            u = active(s, p.telegram_id)
            slot = s.scalar(select(Slot).where(Slot.id == p.slot_id,
                            Slot.owner_id == u.id).with_for_update())
            if not slot:
                raise HTTPException(404, "Unknown slot")
            slot.enabled = False
        return {"ok": True}

    @app.get("/p/{slug}", response_class=HTMLResponse)
    def preview(slug: str, t: str = "", s: Session = Depends(db)):
        slot = s.scalar(select(Slot).where(Slot.slug == slug, Slot.enabled.is_(True)))
        if not slot or not t or not hmac.compare_digest(
                keyed_hash(t, config.key_pepper), slot.review_token_hash):
            raise HTTPException(404, "Not found")
        owner = s.get(User, slot.owner_id)
        if not owner or owner.suspended or owner.active_until <= now():
            raise HTTPException(404, "Not found")
        title = escape(slot.title)
        return HTMLResponse(
            '<!doctype html><html><head><meta name="robots" content="noindex">'
            '<meta name="referrer" content="no-referrer"></head><body>'
            '<main><h1>' + title + '</h1><p>Private project placeholder.</p>'
            '<p>No template or APK is attached. Downloads are disabled.</p>'
            '<small>Independent review prototype, not an official app store.</small>'
            '</main></body></html>',
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                     "X-Robots-Tag": "noindex, nofollow"})
    return app

app = create_app()
