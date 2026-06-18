from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, Field
from app import models
from app.database import get_db

router = APIRouter()


class ShipIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=50)
    draft: float = Field(..., gt=0)
    priority: int = Field(default=0, ge=0)


class ShipOut(ShipIn):
    id: int

    class Config:
        from_attributes = True


@router.get("", response_model=List[ShipOut])
def list_ships(db: Session = Depends(get_db)):
    return db.query(models.Ship).order_by(models.Ship.code).all()


@router.get("/{ship_id}", response_model=ShipOut)
def get_ship(ship_id: int, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")
    return ship


@router.post("", response_model=ShipOut)
def create_ship(ship_in: ShipIn, db: Session = Depends(get_db)):
    existing = db.query(models.Ship).filter(models.Ship.code == ship_in.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="船只编号已存在，不能重复")
    ship = models.Ship(**ship_in.dict())
    db.add(ship)
    db.commit()
    db.refresh(ship)
    return ship


@router.put("/{ship_id}", response_model=ShipOut)
def update_ship(ship_id: int, ship_in: ShipIn, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    existing = (
        db.query(models.Ship)
        .filter(models.Ship.code == ship_in.code, models.Ship.id != ship_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="船只编号已存在，不能重复")

    for key, value in ship_in.dict().items():
        setattr(ship, key, value)
    db.commit()
    db.refresh(ship)

    _invalidate_ship_schedules(db, ship_id)

    return ship


@router.delete("/{ship_id}")
def delete_ship(ship_id: int, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")
    db.delete(ship)
    db.commit()
    return {"ok": True}


def _invalidate_ship_schedules(db: Session, ship_id: int):
    from app.scheduler import auto_recalculate_schedules
    auto_recalculate_schedules(db, target_ship_ids=[ship_id])
