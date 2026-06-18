from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime
from app import models
from app.database import get_db
from app import scheduler

router = APIRouter()


class ScheduleGenerateIn(BaseModel):
    ship_id: int
    dock_id: int
    from_date: date
    to_date: date


class ScheduleSaveIn(BaseModel):
    ship_id: int
    dock_id: int
    enter_time: datetime
    start_drain_time: datetime
    start_repair_time: datetime
    start_oil_time: Optional[datetime] = None
    exit_time: datetime


class ScheduleOut(BaseModel):
    id: int
    ship_id: int
    ship_code: str
    ship_name: str
    dock_id: int
    dock_code: str
    dock_name: str
    enter_time: datetime
    start_drain_time: datetime
    start_repair_time: datetime
    start_oil_time: Optional[datetime]
    exit_time: datetime
    status: str
    created_at: datetime
    durations: Optional[dict] = None

    class Config:
        from_attributes = True


@router.get("", response_model=List[ScheduleOut])
def list_schedules(db: Session = Depends(get_db)):
    schedules = (
        db.query(models.Schedule)
        .order_by(models.Schedule.enter_time)
        .all()
    )
    result = []
    for s in schedules:
        durations = scheduler.get_process_durations(db, s.ship_id)
        result.append({
            "id": s.id,
            "ship_id": s.ship_id,
            "ship_code": s.ship.code if s.ship else "",
            "ship_name": s.ship.name if s.ship else "",
            "dock_id": s.dock_id,
            "dock_code": s.dock.code if s.dock else "",
            "dock_name": s.dock.name if s.dock else "",
            "enter_time": s.enter_time,
            "start_drain_time": s.start_drain_time,
            "start_repair_time": s.start_repair_time,
            "start_oil_time": s.start_oil_time,
            "exit_time": s.exit_time,
            "status": s.status,
            "created_at": s.created_at,
            "durations": durations,
        })
    return result


@router.post("/preview")
def preview_schedule(data: ScheduleGenerateIn, db: Session = Depends(get_db)):
    if data.from_date > data.to_date:
        raise HTTPException(status_code=400, detail="起始日期不能晚于结束日期")
    result = scheduler.generate_schedule(
        db, data.ship_id, data.dock_id, data.from_date, data.to_date
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "排程生成失败"))
    return result


@router.post("", response_model=ScheduleOut)
def save_schedule(data: ScheduleSaveIn, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == data.ship_id).first()
    dock = db.query(models.Dock).filter(models.Dock.id == data.dock_id).first()
    if not ship or not dock:
        raise HTTPException(status_code=404, detail="船只或船坞不存在")

    required_level = max(ship.draft, dock.min_water_level)
    from datetime import timedelta as _td
    enter_date = data.enter_time.date()
    exit_date = data.exit_time.date()

    complete, issues = scheduler.check_tide_data_complete(db, enter_date, exit_date)
    if not complete:
        raise HTTPException(
            status_code=400,
            detail="潮位数据缺失，不能生成正式排程：" + "；".join(issues)
        )

    tides_in_range = scheduler.get_sorted_tides(
        db, enter_date - _td(days=1), exit_date + _td(days=1)
    )
    if len(tides_in_range) < 2:
        raise HTTPException(status_code=400, detail="潮位数据缺失，不能生成正式排程")

    enter_level = scheduler.get_water_level_at(tides_in_range, data.enter_time)
    exit_level = scheduler.get_water_level_at(tides_in_range, data.exit_time)

    if enter_level is None or enter_level < required_level:
        raise HTTPException(status_code=400, detail="进坞时水位不满足吃水要求")
    if exit_level is None or exit_level < required_level:
        raise HTTPException(status_code=400, detail="出坞时水位不满足吃水要求")

    durations = scheduler.get_process_durations(db, data.ship_id)
    total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]
    if total_process_hours <= 0:
        raise HTTPException(status_code=400, detail="请先配置修船工序")

    from datetime import timedelta

    expected_repair_start = data.start_drain_time + timedelta(hours=durations["排水"])
    if data.start_repair_time < expected_repair_start:
        raise HTTPException(status_code=400, detail="修补开始时间不能早于排水完成时间")

    if durations["上油"] > 0:
        expected_oil_start = data.start_repair_time + timedelta(hours=durations["修补"])
        if data.start_oil_time is None or data.start_oil_time < expected_oil_start:
            raise HTTPException(status_code=400, detail="上油开始时间不能早于修补完成时间")
        expected_exit = data.start_oil_time + timedelta(hours=durations["上油"])
    else:
        expected_exit = data.start_repair_time + timedelta(hours=durations["修补"])

    if data.exit_time < expected_exit:
        raise HTTPException(status_code=400, detail="出坞时间不能早于所有必需工序完成时间")

    db.query(models.Schedule).filter(models.Schedule.ship_id == data.ship_id).delete()

    schedule = models.Schedule(
        **data.dict(),
        status="confirmed",
        created_at=datetime.now()
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return {
        "id": schedule.id,
        "ship_id": schedule.ship_id,
        "ship_code": ship.code,
        "ship_name": ship.name,
        "dock_id": schedule.dock_id,
        "dock_code": dock.code,
        "dock_name": dock.name,
        "enter_time": schedule.enter_time,
        "start_drain_time": schedule.start_drain_time,
        "start_repair_time": schedule.start_repair_time,
        "start_oil_time": schedule.start_oil_time,
        "exit_time": schedule.exit_time,
        "status": schedule.status,
        "created_at": schedule.created_at,
        "durations": durations,
    }


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="排程不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.delete("/all/invalid")
def clear_invalid_schedules(db: Session = Depends(get_db)):
    db.query(models.Schedule).filter(models.Schedule.status == "draft").delete()
    db.commit()
    return {"ok": True}
