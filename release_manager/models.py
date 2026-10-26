from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default='')
    active_until: Mapped[int] = mapped_column(BigInteger, default=0)
    quota: Mapped[int] = mapped_column(Integer, default=0)  # legacy: landing-page slots
    apk_quota: Mapped[int] = mapped_column(Integer, default=0)
    storage_quota_mb: Mapped[int] = mapped_column(Integer, default=0)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)


class AccessKey(Base):
    __tablename__ = 'access_keys'
    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    duration_days: Mapped[int] = mapped_column(Integer)
    quota: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger)


class Redemption(Base):
    __tablename__ = 'redemptions'
    __table_args__ = (UniqueConstraint('user_id', 'key_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    key_id: Mapped[int] = mapped_column(ForeignKey('access_keys.id'))


class Slot(Base):
    __tablename__ = 'slots'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(90))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    review_token_hash: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str] = mapped_column(String(64))
    approved_release_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Release(Base):
    __tablename__ = 'releases'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey('slots.id'), index=True)
    original_filename: Mapped[str] = mapped_column(String(180))
    storage_filename: Mapped[str] = mapped_column(String(90), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default='pending')
    package_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    version_name: Mapped[str | None] = mapped_column(String(90), nullable=True)
    signer_sha256: Mapped[str | None] = mapped_column(String(95), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger)


class Defaults(Base):
    __tablename__ = 'defaults'
    id: Mapped[int] = mapped_column(primary_key=True)
    slots: Mapped[int] = mapped_column(Integer, default=2)
    apks: Mapped[int] = mapped_column(Integer, default=2)
    storage_mb: Mapped[int] = mapped_column(Integer, default=100)
    public_base_url: Mapped[str] = mapped_column(String(255), default='')


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger)
