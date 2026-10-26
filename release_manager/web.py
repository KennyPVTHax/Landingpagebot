"""Internal bot API and clearly labeled private APK-release review pages.

Do not expose /internal to the public internet. Deploy behind loopback or a
separate authenticated service. A neutral placeholder does not confer trust.
"""
from contextlib import asynccontextmanager
from pathlib import Path
import hmac
import os
import secrets
import time
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .apk import InvalidApk, digest_file, verified_metadata
from .models import AccessKey, Audit, Base, Defaults, Redemption, Release, Slot, User
from .security import now, token, keyed_hash
from .settings import Settings
from .templates import available_templates, asset_path, render


class Identity(BaseModel):
    telegram_id: int = Field(gt=0)


class Redeem(Identity):
    key: str = Field(min_length=10, max_length=150)


class Issue(Identity):
    days: int = Field(ge=1, le=3650)
    slots: int = Field(ge=0, le=100)
    uses: int = Field(default=1, ge=1, le=1000)


class Grant(Identity):
    target_id: int = Field(gt=0)
    days: int = Field(ge=1, le=3650)
    slots: int = Field(ge=0, le=100)


class ChangeQuota(Identity):
    target_id: int = Field(gt=0)
    slots: int = Field(ge=0, le=100)
    apks: int = Field(ge=0, le=100)
    storage_mb: int = Field(ge=0, le=10240)


class GlobalQuota(Identity):
    slots: int = Field(ge=0, le=100)
    apks: int = Field(ge=0, le=100)
    storage_mb: int = Field(ge=0, le=10240)


class Expiry(Identity):
    target_id: int = Field(gt=0)
    days_from_now: int = Field(ge=0, le=3650)


class Suspend(Identity):
    target_id: int = Field(gt=0)
    value: bool


class NewSlot(Identity):
    title: str = Field(default='Developer project', min_length=2, max_length=90)
    template_id: str = Field(min_length=2, max_length=48)


class SlotAction(Identity):
    slot_id: int = Field(gt=0)


class SetTemplate(SlotAction):
    template_id: str


class ReleaseAction(Identity):
    release_id: int = Field(gt=0)


class ReleaseApproval(ReleaseAction):
    # Deprecated in V3: releases are auto-published after signed APK validation.
    acknowledged_publisher_rights: bool


def create_app(config: Settings | None = None):
    config = config or Settings()
    sqlite = config.database_url.startswith('sqlite')
    engine = create_engine(config.database_url, pool_pre_ping=True,
                           connect_args={'check_same_thread': False} if sqlite else {})
    if sqlite:
        from sqlalchemy import event

        @event.listens_for(engine, 'connect')
        def sqlite_fk(dbapi_connection, connection_record):
            dbapi_connection.execute('PRAGMA foreign_keys=ON')
    sessions = sessionmaker(engine, expire_on_commit=False)
    root = Path(config.private_upload_dir).resolve()
    template_root = Path(config.owner_template_dir).resolve()

    @asynccontextmanager
    async def lifetime(app):
        root.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            root.chmod(0o700)
        Base.metadata.create_all(engine)
        with sessions.begin() as session:
            if session.get(Defaults, 1) is None:
                session.add(Defaults(id=1, slots=2, apks=2, storage_mb=100, public_base_url=''))
        yield
        engine.dispose()

    app = FastAPI(title='Release Manager', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifetime)

    def db():
        with sessions() as session:
            yield session

    def internal(x_internal_token: str | None = Header(default=None)):
        if not x_internal_token or not hmac.compare_digest(x_internal_token, config.internal_api_token):
            raise HTTPException(401, 'Unauthorized')

    protected = [Depends(internal)]

    def require_admin(telegram_id):
        if telegram_id not in config.admins:
            raise HTTPException(403, 'Admin only')

    def user(s, telegram_id, lock=False):
        stmt = select(User).where(User.telegram_id == telegram_id)
        return s.scalar(stmt.with_for_update() if lock else stmt)

    def active(s, telegram_id):
        u = user(s, telegram_id, lock=True)
        if not u or u.suspended or u.active_until <= now():
            raise HTTPException(403, 'Inactive account')
        return u

    def slot_owned(s, owner, slot_id):
        slot = s.scalar(select(Slot).where(Slot.owner_id == owner.id,
                                         Slot.id == slot_id).with_for_update())
        if slot is None:
            raise HTTPException(404, 'Slot not found')
        return slot

    def audit(s, actor, action, description):
        s.add(Audit(actor_id=actor, action=action, description=description, created_at=now()))

    def quota_dict(u):
        return {'slots': u.quota, 'apks': u.apk_quota, 'storage_mb': u.storage_quota_mb}

    @app.get('/health')
    def health():
        return {'ok': True, 'mode': 'skeleton'}

    @app.post('/internal/status', dependencies=protected)
    def status(p: Identity, s: Session = Depends(db)):
        u = user(s, p.telegram_id)
        slot_count = s.scalar(select(func.count(Slot.id)).where(
            Slot.owner_id == u.id, Slot.enabled.is_(True))) if u else 0
        apk_count = s.scalar(select(func.count(Release.id)).where(
            Release.owner_id == u.id, Release.state != 'rejected')) if u else 0
        used = s.scalar(select(func.coalesce(func.sum(Release.size_bytes), 0)).where(
            Release.owner_id == u.id, Release.state != 'rejected')) if u else 0
        return {'admin': p.telegram_id in config.admins,
                'active': bool(u and u.active_until > now() and not u.suspended),
                'suspended': bool(u and u.suspended),
                'expires_at': u.active_until if u else 0,
                'quota': quota_dict(u) if u else {'slots': 0, 'apks': 0, 'storage_mb': 0},
                'usage': {'slots': slot_count, 'apks': apk_count, 'storage_bytes': used}}

    @app.post('/internal/defaults', dependencies=protected)
    def defaults(p: Identity, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        d = s.get(Defaults, 1)
        return {'slots': d.slots, 'apks': d.apks, 'storage_mb': d.storage_mb}

    class Domain(Identity):
        url: str = Field(max_length=255)

    @app.post('/internal/setdomain', dependencies=protected)
    def setdomain(p: Domain, s: Session = Depends(db)):
        # Domain is owner-controlled. HTTPS must be configured separately on host.
        from urllib.parse import urlsplit
        require_admin(p.telegram_id)
        val = p.url.strip().rstrip('/')
        parsed = urlsplit(val) if val else None
        if val and (parsed.scheme != 'https' or not parsed.hostname or
                    parsed.username or parsed.password or parsed.port or
                    parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise HTTPException(400, 'Use an HTTPS domain only, without path or query')
        with s.begin():
            d = s.get(Defaults, 1, with_for_update=True)
            d.public_base_url = val
            audit(s, p.telegram_id, 'setdomain', val or 'cleared')
        return {'ok': True, 'domain': val, 'dns_required': bool(val)}

    @app.post('/internal/setdefaults', dependencies=protected)
    def setdefaults(p: GlobalQuota, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            d = s.get(Defaults, 1, with_for_update=True)
            d.slots, d.apks, d.storage_mb = p.slots, p.apks, p.storage_mb
            audit(s, p.telegram_id, 'setdefaults', f'{p.slots}/{p.apks}/{p.storage_mb}')
        return {'ok': True, 'defaults': p.model_dump(exclude={'telegram_id'})}

    @app.post('/internal/setallquota', dependencies=protected)
    def setallquota(p: GlobalQuota, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            # Bulk change is intentional; existing files remain, but new uploads are denied above quota.
            count = s.query(User).update({'quota': p.slots, 'apk_quota': p.apks,
                                         'storage_quota_mb': p.storage_mb}, synchronize_session=False)
            audit(s, p.telegram_id, 'setallquota', f'{count} users -> {p.slots}/{p.apks}/{p.storage_mb}')
        return {'ok': True, 'users_updated': count}

    @app.post('/internal/setquota', dependencies=protected)
    def setquota(p: ChangeQuota, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            u = user(s, p.target_id, lock=True)
            if not u:
                raise HTTPException(404, 'User not found')
            u.quota, u.apk_quota, u.storage_quota_mb = p.slots, p.apks, p.storage_mb
            audit(s, p.telegram_id, 'setquota', f'{p.target_id}: {p.slots}/{p.apks}/{p.storage_mb}')
        return {'ok': True, 'quota': quota_dict(u)}

    @app.post('/internal/issue', dependencies=protected)
    def issue(p: Issue, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        raw = 'RM-' + token()
        with s.begin():
            s.add(AccessKey(key_hash=keyed_hash(raw, config.key_pepper),
                            duration_days=p.days, quota=p.slots,
                            remaining=p.uses, expires_at=now() + 30 * 86400))
            audit(s, p.telegram_id, 'issue', f'key issued for {p.days} days, {p.uses} uses')
        return {'key': raw}

    @app.post('/internal/redeem', dependencies=protected)
    def redeem(p: Redeem, s: Session = Depends(db)):
        with s.begin():
            u = user(s, p.telegram_id, lock=True)
            if u and u.suspended:
                raise HTTPException(403, 'Account suspended')
            k = s.execute(update(AccessKey).where(
                AccessKey.key_hash == keyed_hash(p.key.strip(), config.key_pepper),
                AccessKey.remaining > 0, AccessKey.expires_at > now()).values(
                remaining=AccessKey.remaining - 1).returning(
                AccessKey.id, AccessKey.duration_days, AccessKey.quota)).first()
            if not k:
                raise HTTPException(400, 'Key invalid or exhausted')
            if u is None:
                u = User(telegram_id=p.telegram_id)
                s.add(u)
                s.flush()
            already = s.scalar(select(Redemption.id).where(
                Redemption.user_id == u.id, Redemption.key_id == k.id))
            if already:
                raise HTTPException(409, 'Already used by this account')
            d = s.get(Defaults, 1)
            s.add(Redemption(user_id=u.id, key_id=k.id))
            u.quota = max(u.quota, k.quota, d.slots)
            u.apk_quota = max(u.apk_quota, d.apks)
            u.storage_quota_mb = max(u.storage_quota_mb, d.storage_mb)
            u.active_until = max(now(), u.active_until) + k.duration_days * 86400
            audit(s, p.telegram_id, 'redeem', 'Access key redeemed')
        return {'active': True, 'quota': quota_dict(u), 'expires_at': u.active_until}

    @app.post('/internal/grant', dependencies=protected)
    def grant(p: Grant, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            u = user(s, p.target_id, lock=True)
            if u is None:
                u = User(telegram_id=p.target_id)
                s.add(u)
            d = s.get(Defaults, 1)
            u.active_until = max(now(), u.active_until) + p.days * 86400
            u.quota = max(u.quota, p.slots)
            u.apk_quota = max(u.apk_quota, d.apks)
            u.storage_quota_mb = max(u.storage_quota_mb, d.storage_mb)
            u.suspended = False
            audit(s, p.telegram_id, 'grant', f'{p.target_id} granted {p.days} days')
        return {'ok': True}

    @app.post('/internal/setexpiry', dependencies=protected)
    def setexpiry(p: Expiry, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            u = user(s, p.target_id, lock=True)
            if u is None:
                raise HTTPException(404, 'User not found')
            u.active_until = now() + p.days_from_now * 86400
            audit(s, p.telegram_id, 'setexpiry', f'{p.target_id}: {p.days_from_now} days')
        return {'ok': True, 'expires_at': u.active_until}

    @app.post('/internal/suspend', dependencies=protected)
    def suspend(p: Suspend, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            u = user(s, p.target_id, lock=True)
            if not u:
                raise HTTPException(404, 'Unknown user')
            u.suspended = p.value
            audit(s, p.telegram_id, 'suspend', f'{p.target_id}: {p.value}')
        return {'ok': True}

    @app.post('/internal/users', dependencies=protected)
    def users(p: Identity, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        rows = s.scalars(select(User).order_by(User.id.desc()).limit(30)).all()
        return {'users': [{'telegram_id': x.telegram_id, 'active_until': x.active_until,
                           'suspended': x.suspended, 'quota': quota_dict(x)} for x in rows]}

    @app.post('/internal/slots', dependencies=protected)
    def newslot(p: NewSlot, s: Session = Depends(db)):
        if p.template_id not in available_templates(template_root):
            raise HTTPException(400, 'Unknown owner-supplied template')
        with s.begin():
            u = active(s, p.telegram_id)
            count = s.scalar(select(func.count(Slot.id)).where(
                Slot.owner_id == u.id, Slot.enabled.is_(True)))
            if count >= u.quota:
                raise HTTPException(403, 'Slot quota reached')
            secret = token()
            slot = Slot(owner_id=u.id, slug=secrets.token_urlsafe(12),
                        title=p.title.strip(), review_token_hash=keyed_hash(secret, config.key_pepper),
                        enabled=True, template_id=p.template_id)
            s.add(slot)
            s.flush()
            info = {'id': slot.id, 'path': f'/p/{slot.slug}?t={secret}', 'slug': slot.slug}
            audit(s, p.telegram_id, 'newslot', f'slot={slot.id}')
        return info

    @app.post('/internal/slots/list', dependencies=protected)
    def listslots(p: Identity, s: Session = Depends(db)):
        u = user(s, p.telegram_id)
        if not u:
            return {'slots': []}
        rows = s.scalars(select(Slot).where(Slot.owner_id == u.id).order_by(Slot.id)).all()
        return {'slots': [{'id': x.id, 'title': x.title, 'enabled': x.enabled,
                           'slug': x.slug, 'template_id': x.template_id,
                           'approved_release_id': x.approved_release_id} for x in rows]}

    @app.post('/internal/slots/disable', dependencies=protected)
    def disable(p: SlotAction, s: Session = Depends(db)):
        with s.begin():
            u = active(s, p.telegram_id)
            slot = slot_owned(s, u, p.slot_id)
            slot.enabled = False
            audit(s, p.telegram_id, 'disable', f'slot={slot.id}')
        return {'ok': True}

    @app.post('/internal/templates', dependencies=protected)
    def templates(p: Identity):
        # No predefined templates; only owner-provided registered bundles.
        return {'templates': [{'id': k, 'label': v} for k, v in available_templates(template_root).items()]}

    @app.post('/internal/slots/template', dependencies=protected)
    def template(p: SetTemplate, s: Session = Depends(db)):
        if p.template_id not in available_templates(template_root):
            raise HTTPException(400, 'Unknown template')
        with s.begin():
            u = active(s, p.telegram_id)
            slot = slot_owned(s, u, p.slot_id)
            if not slot.enabled:
                raise HTTPException(409, 'Disabled slot')
            slot.template_id = p.template_id
            audit(s, p.telegram_id, 'template', f'slot={slot.id}: {p.template_id}')
        return {'ok': True, 'template_id': p.template_id}

    @app.post('/internal/slots/rotate', dependencies=protected)
    def rotate(p: SlotAction, s: Session = Depends(db)):
        with s.begin():
            u = active(s, p.telegram_id)
            slot = slot_owned(s, u, p.slot_id)
            if not slot.enabled:
                raise HTTPException(409, 'Disabled slot')
            secret = token()
            slot.review_token_hash = keyed_hash(secret, config.key_pepper)
            result = {'path': f'/p/{slot.slug}?t={secret}'}
            audit(s, p.telegram_id, 'rotate', f'slot={slot.id}')
        return result

    @app.post('/internal/apk/upload', dependencies=protected)
    async def upload(telegram_id: int = Form(...), slot_id: int = Form(...),
                     document: UploadFile = File(...), s: Session = Depends(db)):
        """Auto-attach after structural and Android signature verification.

        No manual APK inspection/scanning is performed. Uploaded files aren't
        served at all if verification fails. Only approved slot owners can submit.
        """
        if not document.filename or not document.filename.lower().endswith('.apk'):
            raise HTTPException(400, 'Only APK files accepted')
        # Reject unauthorized users before fetching/checking large files.
        with s.begin():
            active(s, telegram_id)
        max_bytes = config.max_upload_mb * 1024 * 1024
        tmp = root / ('.partial-' + uuid.uuid4().hex)
        used = 0
        final_path = None
        committed = False
        try:
            with tmp.open('xb') as sink:
                if os.name == 'posix':
                    tmp.chmod(0o600)
                while True:
                    chunk = await document.read(1024 * 1024)
                    if not chunk:
                        break
                    used += len(chunk)
                    if used > max_bytes:
                        raise HTTPException(413, 'APK exceeds server upload limit')
                    sink.write(chunk)
            # No manual review; signing/structural checks remain automatic.
            try:
                metadata = verified_metadata(tmp, config.apksigner_path, config.aapt_path)
            except InvalidApk as exc:
                raise HTTPException(422, str(exc)) from exc
            checksum = digest_file(tmp)
            with s.begin():
                u = active(s, telegram_id)
                slot = slot_owned(s, u, slot_id)
                if not slot.enabled or slot.template_id not in available_templates(template_root):
                    raise HTTPException(409, 'Slot disabled or template unavailable')
                count = s.scalar(select(func.count(Release.id)).where(
                    Release.owner_id == u.id, Release.state == 'approved'))
                storage = s.scalar(select(func.coalesce(func.sum(Release.size_bytes), 0)).where(
                    Release.owner_id == u.id, Release.state == 'approved'))
                if count >= u.apk_quota or storage + used > u.storage_quota_mb * 1024 * 1024:
                    raise HTTPException(403, 'APK count/storage quota reached')
                saved = uuid.uuid4().hex + '.apk'
                final_path = root / saved
                os.replace(tmp, final_path)
                release = Release(owner_id=u.id, slot_id=slot.id,
                                  original_filename=Path(document.filename).name[:180],
                                  storage_filename=saved, size_bytes=used,
                                  sha256=checksum, state='approved',
                                  package_name=metadata['package_name'],
                                  version_name=metadata['version_name'],
                                  signer_sha256=metadata['signer_sha256'], created_at=now())
                s.add(release)
                s.flush()
                slot.approved_release_id = release.id
                slot.title = metadata['package_name']
                # Newly uploaded APK generates a new review token, making the
                # old link obsolete and protecting against stale downloads.
                secret = token()
                slot.review_token_hash = keyed_hash(secret, config.key_pepper)
                link = f'/p/{slot.slug}?t={secret}'
                domain = s.get(Defaults, 1).public_base_url or config.public_base_url.rstrip('/')
                audit(s, telegram_id, 'auto_publish', f'release={release.id}, slot={slot.id}')
                result = {'id': release.id, 'state': 'approved', 'sha256': checksum,
                          'package_name': release.package_name, 'signer_sha256': release.signer_sha256,
                          'size_bytes': used, 'slot_id': slot.id, 'path': link,
                          'url': domain + link if domain else None,
                          'domain_configured': bool(domain)}
            committed = True
            return result
        except Exception:
            tmp.unlink(missing_ok=True)
            if final_path is not None and not committed:
                final_path.unlink(missing_ok=True)
            raise
        finally:
            await document.close()

    @app.post('/internal/apk/list', dependencies=protected)
    def list_apks(p: Identity, s: Session = Depends(db)):
        u = user(s, p.telegram_id)
        if not u:
            return {'releases': []}
        rows = s.scalars(select(Release).where(Release.owner_id == u.id).order_by(Release.id.desc())).all()
        return {'releases': [{'id': x.id, 'slot_id': x.slot_id, 'filename': x.original_filename,
                              'state': x.state, 'sha256': x.sha256, 'size_bytes': x.size_bytes}
                             for x in rows]}

    @app.post('/internal/apk/revoke', dependencies=protected)
    def revoke(p: ReleaseAction, s: Session = Depends(db)):
        require_admin(p.telegram_id)
        with s.begin():
            rel = s.scalar(select(Release).where(Release.id == p.release_id).with_for_update())
            if not rel:
                raise HTTPException(404, 'Unknown release')
            slot = s.get(Slot, rel.slot_id)
            rel.state = 'revoked'
            if slot.approved_release_id == rel.id:
                slot.approved_release_id = None
            audit(s, p.telegram_id, 'revoke', f'release={rel.id}')
        return {'ok': True}

    def resolve_review(s, slug, t):
        slot = s.scalar(select(Slot).where(Slot.slug == slug, Slot.enabled.is_(True)))
        if not slot or not t or not hmac.compare_digest(
                keyed_hash(t, config.key_pepper), slot.review_token_hash):
            raise HTTPException(404, 'Not found')
        owner = s.get(User, slot.owner_id)
        if not owner or owner.suspended or owner.active_until <= now():
            raise HTTPException(404, 'Not found')
        return slot

    security_headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                        'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff'}

    @app.get('/template-assets/{template_id}/{relative_path:path}')
    def template_asset(template_id: str, relative_path: str):
        asset = asset_path(template_root, template_id, relative_path)
        if not asset:
            raise HTTPException(404, 'Asset not found')
        return FileResponse(asset, headers={'X-Content-Type-Options': 'nosniff',
                                            'Cache-Control': 'public, max-age=300'})

    @app.get('/p/{slug}', response_class=HTMLResponse)
    def preview(slug: str, t: str = '', s: Session = Depends(db)):
        slot = resolve_review(s, slug, t)
        rel = s.get(Release, slot.approved_release_id) if slot.approved_release_id else None
        if rel and rel.state != 'approved':
            rel = None
        if slot.template_id not in available_templates(template_root):
            raise HTTPException(404, 'Template unavailable')
        download = f'/p/{slug}/download?t={t}' if rel else None
        html = render(template_root, slot.template_id, slot.title, rel, download)
        return HTMLResponse(html, headers=security_headers)

    @app.get('/p/{slug}/download')
    def download(slug: str, t: str = '', s: Session = Depends(db)):
        slot = resolve_review(s, slug, t)
        rel = s.get(Release, slot.approved_release_id) if slot.approved_release_id else None
        if not rel or rel.state != 'approved':
            raise HTTPException(404, 'No approved APK available')
        path = root / rel.storage_filename
        if not path.is_file():
            raise HTTPException(404, 'File unavailable')
        return FileResponse(path, filename=rel.original_filename,
                            media_type='application/vnd.android.package-archive',
                            headers={**security_headers, 'Content-Disposition': 'attachment; filename="release.apk"'})

    return app

