from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    active_until: Mapped[int] = mapped_column(BigInteger, default=0)
    quota: Mapped[int] = mapped_column(Integer, default=0)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)

class AccessKey(Base):
    __tablename__ = "access_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    duration_days: Mapped[int] = mapped_column(Integer)
    quota: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger)

class Redemption(Base):
    __tablename__ = "redemptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key_id: Mapped[int] = mapped_column(ForeignKey("access_keys.id"))

class Slot(Base):
    __tablename__ = "slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(90))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    review_token_hash: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # APK release/storage integration is intentionally deferred.
