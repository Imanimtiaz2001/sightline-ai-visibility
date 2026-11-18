"""Brand profile CRUD."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import BrandIn
from app.core.errors import Conflict, NotFound
from app.db.models import Brand, Competitor, Run, RunStatus


def _load(session: Session, brand_id: str) -> Brand:
    brand = session.get(Brand, brand_id, options=[selectinload(Brand.competitors), selectinload(Brand.runs)])
    if brand is None:
        raise NotFound("brand", brand_id)
    return brand


def list_brands(session: Session) -> list[Brand]:
    return list(
        session.scalars(
            select(Brand).options(selectinload(Brand.competitors), selectinload(Brand.runs)).order_by(Brand.name)
        )
    )


def get_brand(session: Session, brand_id: str) -> Brand:
    return _load(session, brand_id)


def _apply(brand: Brand, body: BrandIn) -> None:
    brand.name, brand.domain, brand.market = body.name, body.domain, body.market
    brand.description, brand.location_code, brand.language_code = (
        body.description,
        body.location_code,
        body.language_code,
    )
    brand.seed_topics = list(body.seed_topics)
    existing = {c.domain: c for c in brand.competitors}
    brand.competitors = [
        _competitor(existing.get(c.domain), c.name, c.domain, i) for i, c in enumerate(body.competitors)
    ]


def _competitor(current: Competitor | None, name: str, domain: str, position: int) -> Competitor:
    competitor = current or Competitor(domain=domain)
    competitor.name, competitor.position = name, position
    return competitor


def create_brand(session: Session, body: BrandIn) -> Brand:
    if session.scalar(select(Brand.id).where(Brand.domain == body.domain)):
        raise Conflict(f"a brand with domain '{body.domain}' already exists")
    brand = Brand()
    _apply(brand, body)
    session.add(brand)
    try:
        session.commit()
    except IntegrityError as exc:  # race with a concurrent create
        session.rollback()
        raise Conflict(f"a brand with domain '{body.domain}' already exists") from exc
    return _load(session, brand.id)


def update_brand(session: Session, brand_id: str, body: BrandIn) -> Brand:
    """Profile edits never rewrite history: every run keeps the snapshot it measured."""
    brand = _load(session, brand_id)
    clash = session.scalar(select(Brand.id).where(Brand.domain == body.domain, Brand.id != brand_id))
    if clash:
        raise Conflict(f"a brand with domain '{body.domain}' already exists")
    _apply(brand, body)
    session.commit()
    return _load(session, brand_id)


def delete_brand(session: Session, brand_id: str) -> None:
    brand = _load(session, brand_id)
    active = session.scalar(
        select(Run.id).where(
            Run.brand_id == brand_id, Run.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value])
        )
    )
    if active:
        raise Conflict("cannot delete a brand while an analysis is running")
    session.delete(brand)
    session.commit()
