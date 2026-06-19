import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
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


class BatchScheduleGenerateIn(BaseModel):
    ship_ids: List[int]
    dock_ids: List[int]
    from_date: date
    to_date: date


class BatchScheduleSaveIn(BaseModel):
    schedules: List[dict]


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
    ship_priority: int
    dock_id: int
    dock_code: str
    dock_name: str
    enter_time: datetime
    start_drain_time: datetime
    start_repair_time: datetime
    start_oil_time: Optional[datetime]
    exit_time: datetime
    status: str
    conflict_reason: Optional[str]
    created_at: datetime
    durations: Optional[dict] = None
    required_level: Optional[float] = None

    class Config:
        from_attributes = True


def _schedule_to_dict(s, db=None) -> dict:
    durations = scheduler.get_process_durations(db, s.ship_id) if db and s.dock_id > 0 else None
    required_level = None
    if db and s.dock_id > 0:
        ship = db.query(models.Ship).filter(models.Ship.id == s.ship_id).first()
        dock = db.query(models.Dock).filter(models.Dock.id == s.dock_id).first()
        if ship and dock:
            required_level = max(ship.draft, dock.min_water_level)
    return {
        "id": s.id,
        "ship_id": s.ship_id,
        "ship_code": s.ship.code if s.ship else "",
        "ship_name": s.ship.name if s.ship else "",
        "ship_priority": s.ship.priority if s.ship else 0,
        "dock_id": s.dock_id,
        "dock_code": s.dock.code if s.dock and s.dock_id > 0 else "",
        "dock_name": s.dock.name if s.dock and s.dock_id > 0 else "",
        "enter_time": s.enter_time,
        "start_drain_time": s.start_drain_time,
        "start_repair_time": s.start_repair_time,
        "start_oil_time": s.start_oil_time,
        "exit_time": s.exit_time,
        "status": s.status,
        "conflict_reason": s.conflict_reason,
        "created_at": s.created_at,
        "durations": durations,
        "required_level": required_level,
    }


@router.get("", response_model=List[ScheduleOut])
def list_schedules(
    status: Optional[str] = Query(None, description="Filter by status: draft/confirmed/conflict"),
    dock_id: Optional[int] = Query(None, description="Filter by dock"),
    ship_id: Optional[int] = Query(None, description="Filter by ship"),
    from_date: Optional[date] = Query(None, description="Filter from date"),
    to_date: Optional[date] = Query(None, description="Filter to date"),
    db: Session = Depends(get_db)
):
    q = db.query(models.Schedule)

    if status:
        q = q.filter(models.Schedule.status == status)
    if dock_id:
        q = q.filter(models.Schedule.dock_id == dock_id)
    if ship_id:
        q = q.filter(models.Schedule.ship_id == ship_id)
    if from_date:
        q = q.filter(models.Schedule.enter_time >= from_date)
    if to_date:
        q = q.filter(models.Schedule.exit_time <= to_date + __import__("datetime").timedelta(days=1))

    schedules = q.order_by(models.Schedule.enter_time).all()
    return [_schedule_to_dict(s, db) for s in schedules]


@router.get("/unscheduled-ships")
def get_unscheduled_ships(db: Session = Depends(get_db)):
    return scheduler.get_unscheduled_ships(db)


@router.get("/csv")
def export_schedules_csv(
    status: Optional[str] = Query(None),
    dock_id: Optional[int] = Query(None),
    ship_id: Optional[int] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    include_details: bool = Query(True, description="Include detailed process information"),
    db: Session = Depends(get_db)
):
    schedules = list_schedules(status=status, dock_id=dock_id, ship_id=ship_id, from_date=from_date, to_date=to_date, db=db)

    output = io.StringIO()
    writer = csv.writer(output)

    if include_details:
        writer.writerow([
            "排程ID", "船只编号", "船只名称", "优先级", "吃水(m)",
            "船坞编号", "船坞名称", "船坞最低水位(m)", "要求水位(m)",
            "进坞时间", "排水开始", "修补开始", "上油开始", "出坞时间",
            "总耗时(h)", "工序总时长(h)", "排水时长(h)", "修补时长(h)", "上油时长(h)",
            "状态", "冲突原因", "创建时间"
        ])
    else:
        writer.writerow([
            "排程ID", "船只编号", "船只名称", "船坞编号", "船坞名称",
            "进坞时间", "出坞时间", "状态", "冲突原因"
        ])

    status_map = {"draft": "草稿", "confirmed": "已确认", "conflict": "冲突"}
    for s in schedules:
        durations = s.get("durations") or {}
        total_hours = ""
        process_hours = ""
        if s.get("enter_time") and s.get("exit_time"):
            total_seconds = (s["exit_time"] - s["enter_time"]).total_seconds()
            total_hours = round(total_seconds / 3600, 2)
            process_hours = round(sum(durations.get(k, 0) for k in ["排水", "修补", "上油"]), 2)

        ship = db.query(models.Ship).filter(models.Ship.id == s["ship_id"]).first()
        dock = db.query(models.Dock).filter(models.Dock.id == s["dock_id"]).first()

        if include_details:
            writer.writerow([
                s["id"],
                s["ship_code"],
                s["ship_name"],
                s.get("ship_priority", 0),
                ship.draft if ship else "",
                s["dock_code"],
                s["dock_name"],
                dock.min_water_level if dock else "",
                s.get("required_level") or "",
                s["enter_time"].strftime("%Y-%m-%d %H:%M") if s["enter_time"] else "",
                s["start_drain_time"].strftime("%Y-%m-%d %H:%M") if s["start_drain_time"] else "",
                s["start_repair_time"].strftime("%Y-%m-%d %H:%M") if s["start_repair_time"] else "",
                s["start_oil_time"].strftime("%Y-%m-%d %H:%M") if s.get("start_oil_time") else "",
                s["exit_time"].strftime("%Y-%m-%d %H:%M") if s["exit_time"] else "",
                total_hours,
                process_hours,
                durations.get("排水", ""),
                durations.get("修补", ""),
                durations.get("上油", ""),
                status_map.get(s["status"], s["status"]),
                s.get("conflict_reason") or "",
                s["created_at"].strftime("%Y-%m-%d %H:%M:%S") if s.get("created_at") else "",
            ])
        else:
            writer.writerow([
                s["id"],
                s["ship_code"],
                s["ship_name"],
                s["dock_code"],
                s["dock_name"],
                s["enter_time"].strftime("%Y-%m-%d %H:%M") if s["enter_time"] else "",
                s["exit_time"].strftime("%Y-%m-%d %H:%M") if s["exit_time"] else "",
                status_map.get(s["status"], s["status"]),
                s.get("conflict_reason") or "",
            ])

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=schedules_{timestamp}.csv"}
    )


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


@router.post("/batch-preview")
def batch_preview_schedule(data: BatchScheduleGenerateIn, db: Session = Depends(get_db)):
    if data.from_date > data.to_date:
        raise HTTPException(status_code=400, detail="起始日期不能晚于结束日期")
    if not data.ship_ids:
        raise HTTPException(status_code=400, detail="请选择至少一艘船只")
    if not data.dock_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个船坞")

    result = scheduler.batch_generate_schedule(
        db, data.ship_ids, data.dock_ids, data.from_date, data.to_date
    )

    if not result["success"]:
        error_detail = result.get("error", "批量排程生成失败")
        if "issues" in result:
            error_detail += "：" + "；".join(result["issues"])
        raise HTTPException(status_code=400, detail=error_detail)

    dock_stats = result.get("dock_statistics", {})
    for dock_id, stats in dock_stats.items():
        dock = db.query(models.Dock).filter(models.Dock.id == int(dock_id)).first()
        if dock:
            stats["dock_code"] = dock.code
            stats["dock_name"] = dock.name

    return result


def _parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return None


@router.post("/batch-save")
def batch_save_schedule(data: BatchScheduleSaveIn, db: Session = Depends(get_db)):
    saved = []
    errors = []

    for item in data.schedules:
        ship_id = item.get("ship_id")
        ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
        dock = db.query(models.Dock).filter(models.Dock.id == item.get("dock_id")).first()
        if not ship or not dock:
            errors.append({"ship_id": ship_id, "error": "船只或船坞不存在"})
            continue

        enter_time = _parse_datetime(item.get("enter_time"))
        start_drain_time = _parse_datetime(item.get("start_drain_time"))
        start_repair_time = _parse_datetime(item.get("start_repair_time"))
        start_oil_time = _parse_datetime(item.get("start_oil_time"))
        exit_time = _parse_datetime(item.get("exit_time"))

        if not all([enter_time, start_drain_time, start_repair_time, exit_time]):
            errors.append({"ship_id": ship_id, "error": "时间格式无效"})
            continue

        db.query(models.Schedule).filter(
            models.Schedule.ship_id == ship_id,
            models.Schedule.status.in_(["draft", "conflict"])
        ).delete()

        schedule = models.Schedule(
            ship_id=ship_id,
            dock_id=item["dock_id"],
            enter_time=enter_time,
            start_drain_time=start_drain_time,
            start_repair_time=start_repair_time,
            start_oil_time=start_oil_time,
            exit_time=exit_time,
            status="draft",
            conflict_reason=None,
            created_at=datetime.now()
        )
        db.add(schedule)
        db.flush()
        from app.routers.inspections import _sync_tasks_for_schedule
        _sync_tasks_for_schedule(db, schedule)
        saved.append(item.get("ship_code", ""))

    db.commit()
    return {"saved": len(saved), "errors": errors, "saved_ships": saved}


@router.post("/confirm-drafts")
def confirm_draft_schedules(db: Session = Depends(get_db)):
    from app.routers.inspections import check_ship_high_risk_unrectified, _sync_tasks_for_schedule
    draft_schedules = db.query(models.Schedule).filter(models.Schedule.status == "draft").all()

    confirmed = 0
    for s in draft_schedules:
        ship = db.query(models.Ship).filter(models.Ship.id == s.ship_id).first()
        dock = db.query(models.Dock).filter(models.Dock.id == s.dock_id).first()
        if not ship or not dock:
            continue

        high_risk = check_ship_high_risk_unrectified(db, s.ship_id)
        if high_risk:
            s.conflict_reason = "存在未整改的高风险隐患，不能确认为正式排程：" + "；".join(high_risk)
            s.status = "conflict"
            continue

        resource_ok, resource_issues = scheduler.check_schedule_resources(
            db, s.ship_id, s.enter_time.date(), s.exit_time.date(),
            exclude_schedule_ids=[s.id]
        )
        if not resource_ok:
            s.conflict_reason = "资源不足，不能确认为正式排程：" + "；".join(resource_issues)
            s.status = "conflict"
            continue

        required_level = max(ship.draft, dock.min_water_level)
        from datetime import timedelta as _td

        enter_date = s.enter_time.date()
        exit_date = s.exit_time.date()

        complete, issues = scheduler.check_tide_data_complete(db, enter_date, exit_date)
        if not complete:
            s.conflict_reason = "潮位数据缺失，不能确认为正式排程：" + "；".join(issues)
            s.status = "conflict"
            continue

        tides_in_range = scheduler.get_sorted_tides(
            db, enter_date - _td(days=1), exit_date + _td(days=1)
        )
        if len(tides_in_range) < 2:
            s.conflict_reason = "潮位数据缺失，不能确认为正式排程"
            s.status = "conflict"
            continue

        enter_level = scheduler.get_water_level_at(tides_in_range, s.enter_time)
        exit_level = scheduler.get_water_level_at(tides_in_range, s.exit_time)

        if enter_level is None or enter_level < required_level:
            s.conflict_reason = f"进坞时水位({enter_level:.2f}m)不满足要求(≥{required_level}m)"
            s.status = "conflict"
            continue
        if exit_level is None or exit_level < required_level:
            s.conflict_reason = f"出坞时水位({exit_level:.2f}m)不满足要求(≥{required_level}m)"
            s.status = "conflict"
            continue

        overlapping = db.query(models.Schedule).filter(
            models.Schedule.id != s.id,
            models.Schedule.dock_id == s.dock_id,
            models.Schedule.status == "confirmed",
            models.Schedule.enter_time < s.exit_time,
            models.Schedule.exit_time > s.enter_time,
        ).first()
        if overlapping:
            s.conflict_reason = f"与 {overlapping.ship.code if overlapping.ship else '?'} 的排程存在船坞{dock.code}冲突"
            s.status = "conflict"
            continue

        s.status = "confirmed"
        s.conflict_reason = None
        _sync_tasks_for_schedule(db, s)
        confirmed += 1

    db.commit()
    conflict_count = db.query(models.Schedule).filter(models.Schedule.status == "conflict").count()
    return {"confirmed": confirmed, "conflicts": conflict_count}


def _confirm_single_schedule(db: Session, schedule: models.Schedule) -> Tuple[bool, Optional[str]]:
    from app.routers.inspections import check_ship_high_risk_unrectified, _sync_tasks_for_schedule
    ship = db.query(models.Ship).filter(models.Ship.id == schedule.ship_id).first()
    dock = db.query(models.Dock).filter(models.Dock.id == schedule.dock_id).first()
    if not ship or not dock:
        return False, "船只或船坞不存在"

    high_risk = check_ship_high_risk_unrectified(db, schedule.ship_id)
    if high_risk:
        return False, "存在未整改的高风险隐患，不能确认为正式排程：" + "；".join(high_risk)

    resource_ok, resource_issues = scheduler.check_schedule_resources(
        db, schedule.ship_id, schedule.enter_time.date(), schedule.exit_time.date(),
        exclude_schedule_ids=[schedule.id]
    )
    if not resource_ok:
        return False, "资源不足，不能确认为正式排程：" + "；".join(resource_issues)

    required_level = max(ship.draft, dock.min_water_level)
    from datetime import timedelta as _td

    enter_date = schedule.enter_time.date()
    exit_date = schedule.exit_time.date()

    complete, issues = scheduler.check_tide_data_complete(db, enter_date, exit_date)
    if not complete:
        return False, "潮位数据缺失，不能确认为正式排程：" + "；".join(issues)

    tides_in_range = scheduler.get_sorted_tides(
        db, enter_date - _td(days=1), exit_date + _td(days=1)
    )
    if len(tides_in_range) < 2:
        return False, "潮位数据缺失，不能确认为正式排程"

    enter_level = scheduler.get_water_level_at(tides_in_range, schedule.enter_time)
    exit_level = scheduler.get_water_level_at(tides_in_range, schedule.exit_time)

    if enter_level is None or enter_level < required_level:
        return False, f"进坞时水位({enter_level:.2f}m)不满足要求(≥{required_level}m)"
    if exit_level is None or exit_level < required_level:
        return False, f"出坞时水位({exit_level:.2f}m)不满足要求(≥{required_level}m)"

    overlapping = db.query(models.Schedule).filter(
        models.Schedule.id != schedule.id,
        models.Schedule.dock_id == schedule.dock_id,
        models.Schedule.status == "confirmed",
        models.Schedule.enter_time < schedule.exit_time,
        models.Schedule.exit_time > schedule.enter_time,
    ).first()
    if overlapping:
        conflict_ship = overlapping.ship.code if overlapping.ship else "?"
        return False, f"与 {conflict_ship} 的排程存在船坞{dock.code}冲突"

    schedule.status = "confirmed"
    schedule.conflict_reason = None
    _sync_tasks_for_schedule(db, schedule)
    return True, None


@router.post("/confirm/{schedule_id}")
def confirm_single_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="排程不存在")
    if schedule.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的排程才能确认")

    success, error = _confirm_single_schedule(db, schedule)
    db.commit()

    if not success:
        schedule.status = "conflict"
        schedule.conflict_reason = error
        db.commit()
        return {"success": False, "error": error}

    return {"success": True, "message": "排程确认成功", "schedule_id": schedule_id}


@router.post("/recalculate")
def recalculate_schedules(
    ship_ids: Optional[str] = Query(None, description="Comma-separated ship IDs for targeted recalculation"),
    dock_ids: Optional[str] = Query(None, description="Comma-separated dock IDs for targeted recalculation"),
    db: Session = Depends(get_db)
):
    target_ship_ids = None
    target_dock_ids = None
    if ship_ids:
        target_ship_ids = [int(x) for x in ship_ids.split(",") if x.strip().isdigit()]
    if dock_ids:
        target_dock_ids = [int(x) for x in dock_ids.split(",") if x.strip().isdigit()]

    result = scheduler.auto_recalculate_schedules(
        db,
        target_ship_ids=target_ship_ids,
        target_dock_ids=target_dock_ids
    )
    return result


@router.post("", response_model=ScheduleOut)
def save_schedule(data: ScheduleSaveIn, db: Session = Depends(get_db)):
    from app.routers.inspections import check_ship_high_risk_unrectified, _sync_tasks_for_schedule
    ship = db.query(models.Ship).filter(models.Ship.id == data.ship_id).first()
    dock = db.query(models.Dock).filter(models.Dock.id == data.dock_id).first()
    if not ship or not dock:
        raise HTTPException(status_code=404, detail="船只或船坞不存在")

    high_risk = check_ship_high_risk_unrectified(db, data.ship_id)
    if high_risk:
        raise HTTPException(status_code=400, detail="存在未整改的高风险隐患，不能生成正式排程：" + "；".join(high_risk))

    resource_ok, resource_issues = scheduler.check_schedule_resources(
        db, data.ship_id, data.enter_time.date(), data.exit_time.date()
    )
    if not resource_ok:
        raise HTTPException(
            status_code=400,
            detail="资源不足，不能生成正式排程：" + "；".join(resource_issues)
        )

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

    db.query(models.Schedule).filter(
        models.Schedule.ship_id == data.ship_id,
        models.Schedule.status.in_(["draft", "conflict"])
    ).delete()

    schedule = models.Schedule(
        **data.dict(),
        status="confirmed",
        conflict_reason=None,
        created_at=datetime.now()
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    from app.routers.costs import recalculate_ship_costs_and_quotations
    recalculate_ship_costs_and_quotations(db, [data.ship_id])
    _sync_tasks_for_schedule(db, schedule)
    db.commit()

    return {
        "id": schedule.id,
        "ship_id": schedule.ship_id,
        "ship_code": ship.code,
        "ship_name": ship.name,
        "ship_priority": ship.priority,
        "dock_id": schedule.dock_id,
        "dock_code": dock.code,
        "dock_name": dock.name,
        "enter_time": schedule.enter_time,
        "start_drain_time": schedule.start_drain_time,
        "start_repair_time": schedule.start_repair_time,
        "start_oil_time": schedule.start_oil_time,
        "exit_time": schedule.exit_time,
        "status": schedule.status,
        "conflict_reason": schedule.conflict_reason,
        "created_at": schedule.created_at,
        "durations": durations,
        "required_level": required_level,
    }


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="排程不存在")
    ship_id = s.ship_id
    db.delete(s)
    db.commit()
    from app.routers.costs import recalculate_ship_costs_and_quotations
    recalculate_ship_costs_and_quotations(db, [ship_id])
    return {"ok": True}


@router.delete("/all/invalid")
def clear_invalid_schedules(db: Session = Depends(get_db)):
    db.query(models.Schedule).filter(
        models.Schedule.status.in_(["draft", "conflict"])
    ).delete()
    db.commit()
    return {"ok": True}
