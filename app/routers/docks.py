from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, Field
from app import models
from app.database import get_db

router = APIRouter()


class DockIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=50)
    min_water_level: float = Field(..., gt=0)


class DockOut(DockIn):
    id: int

    class Config:
        from_attributes = True


@router.get("", response_model=List[DockOut])
def list_docks(db: Session = Depends(get_db)):
    return db.query(models.Dock).order_by(models.Dock.code).all()


@router.post("", response_model=DockOut)
def create_dock(dock_in: DockIn, db: Session = Depends(get_db)):
    existing = db.query(models.Dock).filter(models.Dock.code == dock_in.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="船坞编号已存在，不能重复")
    dock = models.Dock(**dock_in.dict())
    db.add(dock)
    db.commit()
    db.refresh(dock)
    return dock


@router.put("/{dock_id}", response_model=DockOut)
def update_dock(dock_id: int, dock_in: DockIn, db: Session = Depends(get_db)):
    dock = db.query(models.Dock).filter(models.Dock.id == dock_id).first()
    if not dock:
        raise HTTPException(status_code=404, detail="船坞不存在")

    existing = (
        db.query(models.Dock)
        .filter(models.Dock.code == dock_in.code, models.Dock.id != dock_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="船坞编号已存在，不能重复")

    for key, value in dock_in.dict().items():
        setattr(dock, key, value)
    db.commit()
    db.refresh(dock)

    _invalidate_dock_schedules(db, dock_id)

    return dock


@router.delete("/{dock_id}")
def delete_dock(dock_id: int, db: Session = Depends(get_db)):
    dock = db.query(models.Dock).filter(models.Dock.id == dock_id).first()
    if not dock:
        raise HTTPException(status_code=404, detail="船坞不存在")
    db.delete(dock)
    db.commit()
    return {"ok": True}


def _invalidate_dock_schedules(db: Session, dock_id: int):
    db.query(models.Schedule).filter(models.Schedule.dock_id == dock_id).update(
        {models.Schedule.status: "draft"}
    )
    db.commit()
