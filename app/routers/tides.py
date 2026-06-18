from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, Field
from datetime import date, datetime
from app import models
from app.database import get_db

router = APIRouter()


class TideIn(BaseModel):
    tide_date: date
    tide_time: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    water_level: float = Field(..., gt=0)


class TideOut(TideIn):
    id: int

    class Config:
        from_attributes = True


@router.get("", response_model=List[TideOut])
def list_tides(db: Session = Depends(get_db)):
    return (
        db.query(models.Tide)
        .order_by(models.Tide.tide_date, models.Tide.tide_time)
        .all()
    )


@router.get("/by-date", response_model=List[TideOut])
def list_tides_by_date(start_date: date, end_date: date, db: Session = Depends(get_db)):
    return (
        db.query(models.Tide)
        .filter(models.Tide.tide_date >= start_date, models.Tide.tide_date <= end_date)
        .order_by(models.Tide.tide_date, models.Tide.tide_time)
        .all()
    )


@router.post("", response_model=TideOut)
def create_tide(tide_in: TideIn, db: Session = Depends(get_db)):
    _validate_time(tide_in.tide_time)
    tide = models.Tide(**tide_in.dict())
    db.add(tide)
    db.commit()
    db.refresh(tide)
    _invalidate_all_schedules(db)
    return tide


@router.put("/{tide_id}", response_model=TideOut)
def update_tide(tide_id: int, tide_in: TideIn, db: Session = Depends(get_db)):
    tide = db.query(models.Tide).filter(models.Tide.id == tide_id).first()
    if not tide:
        raise HTTPException(status_code=404, detail="潮位记录不存在")
    _validate_time(tide_in.tide_time)
    for key, value in tide_in.dict().items():
        setattr(tide, key, value)
    db.commit()
    db.refresh(tide)
    _invalidate_all_schedules(db)
    return tide


@router.delete("/{tide_id}")
def delete_tide(tide_id: int, db: Session = Depends(get_db)):
    tide = db.query(models.Tide).filter(models.Tide.id == tide_id).first()
    if not tide:
        raise HTTPException(status_code=404, detail="潮位记录不存在")
    db.delete(tide)
    db.commit()
    _invalidate_all_schedules(db)
    return {"ok": True}


@router.post("/batch")
def create_tides_batch(tides_in: List[TideIn], db: Session = Depends(get_db)):
    for t in tides_in:
        _validate_time(t.tide_time)
        tide = models.Tide(**t.dict())
        db.add(tide)
    db.commit()
    _invalidate_all_schedules(db)
    return {"ok": True, "count": len(tides_in)}


def _validate_time(time_str: str):
    try:
        h, m = map(int, time_str.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="时间格式错误，应为 HH:MM")


def _invalidate_all_schedules(db: Session):
    db.query(models.Schedule).update({models.Schedule.status: "draft"})
    db.commit()
