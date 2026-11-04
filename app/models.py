from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo; store naive UTC and hand back aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.astimezone(UTC).replace(tzinfo=None) if value else value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value else value


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    app_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    versions: Mapped[list["Version"]] = relationship(
        back_populates="game", order_by="Version.number.desc()", cascade="all, delete-orphan"
    )


class Version(Base):
    """A full snapshot of a game's cloud saves at one point in time."""

    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("app_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("games.app_id", ondelete="CASCADE"), index=True)
    number: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    total_size: Mapped[int] = mapped_column(BigInteger)

    game: Mapped[Game] = relationship(back_populates="versions")
    files: Mapped[list["VersionFile"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="VersionFile.path"
    )


class VersionFile(Base):
    __tablename__ = "version_files"
    __table_args__ = (UniqueConstraint("version_id", "folder", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), index=True)
    folder: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)  # real size of the stored bytes
    listed_size: Mapped[int] = mapped_column(BigInteger)  # rounded size Steam showed; change detection only
    modified: Mapped[datetime] = mapped_column(UTCDateTime)  # Steam's "Date Written"
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    version: Mapped[Version] = relationship(back_populates="files")
