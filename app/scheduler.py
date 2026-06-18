from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from app import models


def parse_tide_time(tide_date: date, time_str: str) -> datetime:
    h, m = map(int, time_str.split(":"))
    return datetime.combine(tide_date, datetime.min.time()).replace(hour=h, minute=m)


def get_sorted_tides(db: Session, start_date: date, end_date: date) -> List[models.Tide]:
    tides = (
        db.query(models.Tide)
        .filter(models.Tide.tide_date >= start_date, models.Tide.tide_date <= end_date)
        .all()
    )
    tides.sort(key=lambda t: parse_tide_time(t.tide_date, t.tide_time))
    return tides


def check_tide_data_complete(db: Session, start_date: date, end_date: date) -> Tuple[bool, List[str]]:
    issues = []
    current = start_date
    while current <= end_date:
        day_tides = db.query(models.Tide).filter(models.Tide.tide_date == current).all()
        if len(day_tides) < 2:
            issues.append(f"{current.isoformat()} 潮位数据不足（至少需要2条）")
        current += timedelta(days=1)
    return len(issues) == 0, issues


def find_water_windows(
    tides: List[models.Tide],
    min_level: float,
    start_dt: Optional[datetime] = None
) -> List[Tuple[datetime, datetime]]:
    windows = []
    if len(tides) < 2:
        return windows

    tide_points = [(parse_tide_time(t.tide_date, t.tide_time), t.water_level) for t in tides]

    if start_dt:
        tide_points = [(t, l) for t, l in tide_points if t >= start_dt]

    if len(tide_points) < 2:
        return windows

    for i in range(len(tide_points) - 1):
        t1, l1 = tide_points[i]
        t2, l2 = tide_points[i + 1]
        total_minutes = (t2 - t1).total_seconds() / 60

        above_points = []
        if l1 >= min_level:
            above_points.append(t1)

        if l1 != l2:
            ratio = (min_level - l1) / (l2 - l1)
            if 0 <= ratio <= 1:
                cross_minutes = total_minutes * ratio
                cross_time = t1 + timedelta(minutes=cross_minutes)
                above_points.append(cross_time)

        if l2 >= min_level:
            above_points.append(t2)

        above_points.sort()

        j = 0
        while j < len(above_points) - 1:
            windows.append((above_points[j], above_points[j + 1]))
            j += 2

    return windows


def get_process_durations(db: Session, ship_id: int) -> Dict[str, float]:
    tasks = db.query(models.RepairTask).filter(models.RepairTask.ship_id == ship_id).all()
    result = {"排水": 0.0, "修补": 0.0, "上油": 0.0}
    for t in tasks:
        if t.process_type in result:
            result[t.process_type] = max(result[t.process_type], t.duration_hours)
    return result


def _docks_overlap(
    enter1: datetime, exit1: datetime,
    enter2: datetime, exit2: datetime
) -> bool:
    return enter1 < exit2 and enter2 < exit1


def _check_dock_conflict(
    dock_id: int,
    enter_time: datetime,
    exit_time: datetime,
    existing_schedules: List[Dict]
) -> Optional[Dict]:
    for s in existing_schedules:
        if s["dock_id"] == dock_id and _docks_overlap(enter_time, exit_time, s["enter_time"], s["exit_time"]):
            return s
    return None


def _try_schedule_ship(
    db: Session,
    ship: models.Ship,
    dock: models.Dock,
    from_date: date,
    to_date: date,
    existing_schedules: List[Dict],
    tides: List[models.Tide]
) -> Optional[Dict]:
    required_level = max(ship.draft, dock.min_water_level)
    durations = get_process_durations(db, ship.id)
    total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]

    if total_process_hours <= 0:
        return None

    enter_windows = find_water_windows(tides, required_level)

    for enter_start, enter_end in enter_windows:
        enter_time = enter_start
        drain_end = enter_time + timedelta(hours=durations["排水"])
        repair_start = drain_end
        repair_end = repair_start + timedelta(hours=durations["修补"])
        oil_start = repair_end if durations["上油"] > 0 else None
        oil_end = oil_start + timedelta(hours=durations["上油"]) if oil_start else repair_end
        earliest_exit = oil_end

        exit_windows = find_water_windows(tides, required_level, start_dt=earliest_exit)

        if exit_windows:
            exit_time = exit_windows[0][0]

            conflict = _check_dock_conflict(dock.id, enter_time, exit_time, existing_schedules)
            if conflict:
                continue

            return {
                "success": True,
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
                "ship_priority": ship.priority,
                "dock_id": dock.id,
                "dock_code": dock.code,
                "dock_name": dock.name,
                "enter_time": enter_time,
                "start_drain_time": enter_time,
                "start_repair_time": repair_start,
                "start_oil_time": oil_start,
                "exit_time": exit_time,
                "durations": durations,
                "required_level": required_level
            }

    return None


def batch_generate_schedule(
    db: Session,
    ship_ids: List[int],
    dock_ids: List[int],
    from_date: date,
    to_date: date
) -> Dict:
    if from_date > to_date:
        return {"success": False, "error": "起始日期不能晚于结束日期"}

    ships = db.query(models.Ship).filter(models.Ship.id.in_(ship_ids)).all()
    docks = db.query(models.Dock).filter(models.Dock.id.in_(dock_ids)).all()

    if not ships:
        return {"success": False, "error": "未找到指定船只"}
    if not docks:
        return {"success": False, "error": "未找到指定船坞"}

    ships_sorted = sorted(ships, key=lambda s: (-s.priority, s.id))

    complete, issues = check_tide_data_complete(db, from_date, to_date)
    if not complete:
        return {"success": False, "error": "潮位数据缺失", "issues": issues}

    tides = get_sorted_tides(db, from_date - timedelta(days=1), to_date + timedelta(days=1))
    if len(tides) < 2:
        return {"success": False, "error": "潮位数据不足"}

    scheduled = []
    unassigned = []
    existing_schedules = []

    confirmed_schedules = (
        db.query(models.Schedule)
        .filter(models.Schedule.status == "confirmed")
        .all()
    )
    for cs in confirmed_schedules:
        existing_schedules.append({
            "dock_id": cs.dock_id,
            "enter_time": cs.enter_time,
            "exit_time": cs.exit_time,
            "ship_id": cs.ship_id,
            "ship_code": cs.ship.code if cs.ship else "",
            "dock_code": cs.dock.code if cs.dock else "",
        })

    for ship in ships_sorted:
        durations = get_process_durations(db, ship.id)
        total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]

        if total_process_hours <= 0:
            unassigned.append({
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
                "reason": "未配置修船工序（排水+修补+上油总时长为0）"
            })
            continue

        best_result = None
        best_enter_time = None

        for dock in docks:
            result = _try_schedule_ship(db, ship, dock, from_date, to_date, existing_schedules, tides)
            if result:
                if best_result is None or result["enter_time"] < best_enter_time:
                    best_result = result
                    best_enter_time = result["enter_time"]

        if best_result:
            scheduled.append(best_result)
            existing_schedules.append({
                "dock_id": best_result["dock_id"],
                "enter_time": best_result["enter_time"],
                "exit_time": best_result["exit_time"],
                "ship_id": best_result["ship_id"],
                "ship_code": best_result["ship_code"],
                "dock_code": best_result["dock_code"],
            })
        else:
            required_level = max(ship.draft, min(d.min_water_level for d in docks))
            reasons = []
            if not find_water_windows(tides, required_level):
                reasons.append("在指定日期范围内无满足水位要求的进出坞窗口")
            else:
                reasons.append("所有船坞在可用时间段内均存在冲突，无法安排")
            reasons.append(f"要求水位 ≥ {required_level}m（吃水{ship.draft}m）")
            unassigned.append({
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
                "reason": "；".join(reasons)
            })

    return {
        "success": True,
        "scheduled": scheduled,
        "unassigned": unassigned,
        "total_ships": len(ships_sorted),
        "scheduled_count": len(scheduled),
        "unassigned_count": len(unassigned)
    }


def generate_schedule(
    db: Session,
    ship_id: int,
    dock_id: int,
    from_date: date,
    to_date: date
) -> Dict:
    result = batch_generate_schedule(db, [ship_id], [dock_id], from_date, to_date)
    if not result["success"]:
        return result
    if result["scheduled"]:
        return result["scheduled"][0]
    if result["unassigned"]:
        u = result["unassigned"][0]
        return {"success": False, "error": u["reason"]}
    return {"success": False, "error": "排程生成失败"}


def auto_recalculate_schedules(db: Session) -> Dict:
    draft_schedules = db.query(models.Schedule).filter(models.Schedule.status == "draft").all()

    if not draft_schedules:
        return {"recalculated": 0, "failed": 0, "unchanged": 0}

    ship_ids = list(set(s.ship_id for s in draft_schedules))
    dock_ids = list(set(s.dock_id for s in draft_schedules))
    if not ship_ids or not dock_ids:
        return {"recalculated": 0, "failed": 0, "unchanged": 0}

    min_enter = min(s.enter_time for s in draft_schedules)
    max_exit = max(s.exit_time for s in draft_schedules)
    from_date = min_enter.date() - timedelta(days=1)
    to_date = max_exit.date() + timedelta(days=1)

    for s in draft_schedules:
        db.delete(s)
    db.flush()

    result = batch_generate_schedule(db, ship_ids, dock_ids, from_date, to_date)

    recalculated = 0
    failed = 0
    for item in result.get("scheduled", []):
        schedule = models.Schedule(
            ship_id=item["ship_id"],
            dock_id=item["dock_id"],
            enter_time=item["enter_time"],
            start_drain_time=item["start_drain_time"],
            start_repair_time=item["start_repair_time"],
            start_oil_time=item.get("start_oil_time"),
            exit_time=item["exit_time"],
            status="draft",
            conflict_reason=None,
            created_at=datetime.now()
        )
        db.add(schedule)
        recalculated += 1

    for item in result.get("unassigned", []):
        schedule = models.Schedule(
            ship_id=item["ship_id"],
            dock_id=0,
            enter_time=datetime.now(),
            start_drain_time=datetime.now(),
            start_repair_time=datetime.now(),
            start_oil_time=None,
            exit_time=datetime.now(),
            status="conflict",
            conflict_reason=item["reason"],
            created_at=datetime.now()
        )
        db.add(schedule)
        failed += 1

    db.commit()
    return {"recalculated": recalculated, "failed": failed, "unchanged": 0}


def get_water_level_at(tides: List[models.Tide], target_dt: datetime) -> Optional[float]:
    sorted_tides = sorted(tides, key=lambda t: parse_tide_time(t.tide_date, t.tide_time))

    if len(sorted_tides) == 0:
        return None

    first_dt = parse_tide_time(sorted_tides[0].tide_date, sorted_tides[0].tide_time)
    last_dt = parse_tide_time(sorted_tides[-1].tide_date, sorted_tides[-1].tide_time)

    if target_dt <= first_dt:
        return sorted_tides[0].water_level
    if target_dt >= last_dt:
        return sorted_tides[-1].water_level

    for i in range(len(sorted_tides) - 1):
        t1 = parse_tide_time(sorted_tides[i].tide_date, sorted_tides[i].tide_time)
        t2 = parse_tide_time(sorted_tides[i + 1].tide_date, sorted_tides[i + 1].tide_time)
        if t1 <= target_dt <= t2:
            ratio = (target_dt - t1).total_seconds() / (t2 - t1).total_seconds()
            return sorted_tides[i].water_level + ratio * (sorted_tides[i + 1].water_level - sorted_tides[i].water_level)

    return None
