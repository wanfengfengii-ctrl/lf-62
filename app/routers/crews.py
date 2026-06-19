from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, date, timedelta
from app import models
from app.database import get_db

router = APIRouter()


class CrewIn(BaseModel):
    code: str
    name: str
    crew_type: str = Field(..., pattern=r"^(木工|油工|杂工|起重|其他)$")
    description: Optional[str] = None


class CrewOut(BaseModel):
    id: int
    code: str
    name: str
    crew_type: str
    description: Optional[str] = None
    member_count: int = 0

    class Config:
        from_attributes = True


class CrewMemberIn(BaseModel):
    crew_id: int
    name: str
    phone: Optional[str] = None
    skill_level: Optional[str] = None
    status: str = "在职"


class CrewMemberOut(CrewMemberIn):
    id: int

    class Config:
        from_attributes = True


class DailyAvailabilityIn(BaseModel):
    crew_id: int
    work_date: date
    available_hours: float = Field(..., ge=0)
    used_hours: float = Field(0, ge=0)
    remark: Optional[str] = None


class DailyAvailabilityOut(BaseModel):
    id: int
    crew_id: int
    crew_code: str
    crew_name: str
    crew_type: str
    work_date: date
    available_hours: float
    used_hours: float
    remaining_hours: float
    remark: Optional[str]

    class Config:
        from_attributes = True


@router.get("", response_model=List[CrewOut])
def list_crews(
    crew_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.Crew)
    if crew_type:
        q = q.filter(models.Crew.crew_type == crew_type)
    crews = q.order_by(models.Crew.code).all()
    result = []
    for c in crews:
        result.append({
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "crew_type": c.crew_type,
            "description": c.description,
            "member_count": len(c.members)
        })
    return result


@router.get("/{crew_id}", response_model=CrewOut)
def get_crew(crew_id: int, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "crew_type": c.crew_type,
        "description": c.description,
        "member_count": len(c.members)
    }


@router.post("", response_model=CrewOut)
def create_crew(crew_in: CrewIn, db: Session = Depends(get_db)):
    existing = db.query(models.Crew).filter(models.Crew.code == crew_in.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="班组编码已存在")
    c = models.Crew(**crew_in.dict())
    db.add(c)
    db.commit()
    db.refresh(c)
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "crew_type": c.crew_type,
        "description": c.description,
        "member_count": 0
    }


@router.put("/{crew_id}", response_model=CrewOut)
def update_crew(crew_id: int, crew_in: CrewIn, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    existing = db.query(models.Crew).filter(
        models.Crew.code == crew_in.code,
        models.Crew.id != crew_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="班组编码已存在")
    old_crew_type = c.crew_type
    for key, value in crew_in.dict().items():
        setattr(c, key, value)
    db.commit()
    db.refresh(c)
    if old_crew_type != crew_in.crew_type:
        _recalculate_schedules_by_crew_type(db, old_crew_type)
        _recalculate_schedules_by_crew_type(db, crew_in.crew_type)
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "crew_type": c.crew_type,
        "description": c.description,
        "member_count": len(c.members)
    }


@router.delete("/{crew_id}")
def delete_crew(crew_id: int, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    crew_type = c.crew_type
    db.delete(c)
    db.commit()
    _recalculate_schedules_by_crew_type(db, crew_type)
    return {"ok": True}


@router.get("/{crew_id}/members", response_model=List[CrewMemberOut])
def list_crew_members(crew_id: int, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    return db.query(models.CrewMember).filter(models.CrewMember.crew_id == crew_id).order_by(models.CrewMember.id).all()


@router.post("/members", response_model=CrewMemberOut)
def create_crew_member(member_in: CrewMemberIn, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == member_in.crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    m = models.CrewMember(**member_in.dict())
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.put("/members/{member_id}", response_model=CrewMemberOut)
def update_crew_member(member_id: int, member_in: CrewMemberIn, db: Session = Depends(get_db)):
    m = db.query(models.CrewMember).filter(models.CrewMember.id == member_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="人员不存在")
    old_crew_id = m.crew_id
    for key, value in member_in.dict().items():
        setattr(m, key, value)
    db.commit()
    db.refresh(m)
    if old_crew_id != member_in.crew_id:
        _recalculate_schedules_by_crew(db, old_crew_id)
        _recalculate_schedules_by_crew(db, member_in.crew_id)
    return m


@router.delete("/members/{member_id}")
def delete_crew_member(member_id: int, db: Session = Depends(get_db)):
    m = db.query(models.CrewMember).filter(models.CrewMember.id == member_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="人员不存在")
    crew_id = m.crew_id
    db.delete(m)
    db.commit()
    _recalculate_schedules_by_crew(db, crew_id)
    return {"ok": True}


@router.get("/availability", response_model=List[DailyAvailabilityOut])
def list_daily_availability(
    crew_id: Optional[int] = Query(None),
    crew_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.CrewDailyAvailability).join(models.Crew)
    if crew_id:
        q = q.filter(models.CrewDailyAvailability.crew_id == crew_id)
    if crew_type:
        q = q.filter(models.Crew.crew_type == crew_type)
    if from_date:
        q = q.filter(models.CrewDailyAvailability.work_date >= from_date)
    if to_date:
        q = q.filter(models.CrewDailyAvailability.work_date <= to_date)
    records = q.order_by(models.CrewDailyAvailability.work_date, models.Crew.code).all()
    result = []
    for r in records:
        crew = r.crew
        result.append({
            "id": r.id,
            "crew_id": r.crew_id,
            "crew_code": crew.code if crew else "",
            "crew_name": crew.name if crew else "",
            "crew_type": crew.crew_type if crew else "",
            "work_date": r.work_date,
            "available_hours": r.available_hours,
            "used_hours": r.used_hours,
            "remaining_hours": max(0, r.available_hours - r.used_hours),
            "remark": r.remark
        })
    return result


@router.get("/availability/view")
def get_availability_view(
    from_date: date,
    to_date: date,
    crew_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    crews_q = db.query(models.Crew)
    if crew_type:
        crews_q = crews_q.filter(models.Crew.crew_type == crew_type)
    crews = crews_q.order_by(models.Crew.crew_type, models.Crew.code).all()
    days: List[date] = []
    current = from_date
    while current <= to_date:
        days.append(current)
        current += timedelta(days=1)
    availability_map = {}
    records = db.query(models.CrewDailyAvailability).filter(
        models.CrewDailyAvailability.work_date >= from_date,
        models.CrewDailyAvailability.work_date <= to_date
    ).all()
    for r in records:
        key = (r.crew_id, r.work_date)
        availability_map[key] = r
    crew_views = []
    for c in crews:
        day_data = []
        for d in days:
            key = (c.id, d)
            r = availability_map.get(key)
            if r:
                day_data.append({
                    "date": d,
                    "available_hours": r.available_hours,
                    "used_hours": r.used_hours,
                    "remaining_hours": max(0, r.available_hours - r.used_hours),
                    "remark": r.remark
                })
            else:
                day_data.append({
                    "date": d,
                    "available_hours": 0,
                    "used_hours": 0,
                    "remaining_hours": 0,
                    "remark": None
                })
        crew_views.append({
            "crew_id": c.id,
            "crew_code": c.code,
            "crew_name": c.name,
            "crew_type": c.crew_type,
            "days": day_data
        })
    return {"days": days, "crews": crew_views}


@router.post("/availability", response_model=DailyAvailabilityOut)
def create_daily_availability(avail_in: DailyAvailabilityIn, db: Session = Depends(get_db)):
    c = db.query(models.Crew).filter(models.Crew.id == avail_in.crew_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班组不存在")
    existing = db.query(models.CrewDailyAvailability).filter(
        models.CrewDailyAvailability.crew_id == avail_in.crew_id,
        models.CrewDailyAvailability.work_date == avail_in.work_date
    ).first()
    if existing:
        existing.available_hours = avail_in.available_hours
        existing.used_hours = avail_in.used_hours
        existing.remark = avail_in.remark
        db.commit()
        db.refresh(existing)
        _recalculate_schedules_by_crew(db, avail_in.crew_id)
        crew = existing.crew
        return {
            "id": existing.id,
            "crew_id": existing.crew_id,
            "crew_code": crew.code if crew else "",
            "crew_name": crew.name if crew else "",
            "crew_type": crew.crew_type if crew else "",
            "work_date": existing.work_date,
            "available_hours": existing.available_hours,
            "used_hours": existing.used_hours,
            "remaining_hours": max(0, existing.available_hours - existing.used_hours),
            "remark": existing.remark
        }
    r = models.CrewDailyAvailability(**avail_in.dict())
    db.add(r)
    db.commit()
    db.refresh(r)
    _recalculate_schedules_by_crew(db, avail_in.crew_id)
    crew = r.crew
    return {
        "id": r.id,
        "crew_id": r.crew_id,
        "crew_code": crew.code if crew else "",
        "crew_name": crew.name if crew else "",
        "crew_type": crew.crew_type if crew else "",
        "work_date": r.work_date,
        "available_hours": r.available_hours,
        "used_hours": r.used_hours,
        "remaining_hours": max(0, r.available_hours - r.used_hours),
        "remark": r.remark
    }


class BatchAvailabilityIn(BaseModel):
    crew_ids: List[int]
    from_date: date
    to_date: date
    available_hours: float = 8.0
    skip_weekends: bool = True


@router.post("/availability/batch")
def batch_create_availability(data: BatchAvailabilityIn, db: Session = Depends(get_db)):
    created = 0
    updated = 0
    for crew_id in data.crew_ids:
        c = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
        if not c:
            continue
        current = data.from_date
        while current <= data.to_date:
            if data.skip_weekends and current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            existing = db.query(models.CrewDailyAvailability).filter(
                models.CrewDailyAvailability.crew_id == crew_id,
                models.CrewDailyAvailability.work_date == current
            ).first()
            if existing:
                existing.available_hours = data.available_hours
                updated += 1
            else:
                r = models.CrewDailyAvailability(
                    crew_id=crew_id,
                    work_date=current,
                    available_hours=data.available_hours,
                    used_hours=0
                )
                db.add(r)
                created += 1
            current += timedelta(days=1)
        _recalculate_schedules_by_crew(db, crew_id)
    db.commit()
    return {"created": created, "updated": updated}


@router.get("/alerts/low-hours")
def get_low_hours_alerts(
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    if not from_date:
        from_date = date.today()
    if not to_date:
        to_date = from_date + timedelta(days=7)
    alerts = []
    crews = db.query(models.Crew).order_by(models.Crew.code).all()
    for c in crews:
        records = db.query(models.CrewDailyAvailability).filter(
            models.CrewDailyAvailability.crew_id == c.id,
            models.CrewDailyAvailability.work_date >= from_date,
            models.CrewDailyAvailability.work_date <= to_date
        ).all()
        record_map = {(r.work_date): r for r in records}
        current = from_date
        shortage_days = []
        while current <= to_date:
            r = record_map.get(current)
            if not r or r.available_hours <= 0:
                shortage_days.append(current)
            elif r.available_hours - r.used_hours < 4:
                shortage_days.append(current)
            current += timedelta(days=1)
        if shortage_days:
            alerts.append({
                "crew_id": c.id,
                "crew_code": c.code,
                "crew_name": c.name,
                "crew_type": c.crew_type,
                "shortage_dates": [d.isoformat() for d in shortage_days],
                "shortage_count": len(shortage_days)
            })
    return alerts


def _recalculate_schedules_by_crew(db: Session, crew_id: int):
    from app.scheduler import auto_recalculate_schedules
    crew = db.query(models.Crew).filter(models.Crew.id == crew_id).first()
    if not crew:
        return
    reqs = db.query(models.TaskLaborRequirement).filter(
        (models.TaskLaborRequirement.crew_id == crew_id) |
        (models.TaskLaborRequirement.crew_type == crew.crew_type)
    ).all()
    task_ids = [r.task_id for r in reqs]
    if not task_ids:
        return
    tasks = db.query(models.RepairTask).filter(models.RepairTask.id.in_(task_ids)).all()
    ship_ids = list(set(t.ship_id for t in tasks))
    if ship_ids:
        auto_recalculate_schedules(
            db,
            target_ship_ids=ship_ids,
            trigger_source=f"crew_change:{crew_id}"
        )
    from app.routers.costs import recalculate_ship_costs_and_quotations
    if ship_ids:
        recalculate_ship_costs_and_quotations(db, ship_ids)


def _recalculate_schedules_by_crew_type(db: Session, crew_type: str):
    from app.scheduler import auto_recalculate_schedules
    reqs = db.query(models.TaskLaborRequirement).filter(
        models.TaskLaborRequirement.crew_type == crew_type
    ).all()
    task_ids = [r.task_id for r in reqs]
    if not task_ids:
        return
    tasks = db.query(models.RepairTask).filter(models.RepairTask.id.in_(task_ids)).all()
    ship_ids = list(set(t.ship_id for t in tasks))
    if ship_ids:
        auto_recalculate_schedules(
            db,
            target_ship_ids=ship_ids,
            trigger_source=f"crew_type_change:{crew_type}"
        )
    from app.routers.costs import recalculate_ship_costs_and_quotations
    if ship_ids:
        recalculate_ship_costs_and_quotations(db, ship_ids)
