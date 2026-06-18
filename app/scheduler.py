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


def generate_schedule(
    db: Session,
    ship_id: int,
    dock_id: int,
    from_date: date,
    to_date: date
) -> Dict:
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    dock = db.query(models.Dock).filter(models.Dock.id == dock_id).first()

    if not ship or not dock:
        return {"success": False, "error": "船只或船坞不存在"}

    required_level = max(ship.draft, dock.min_water_level)
    durations = get_process_durations(db, ship_id)
    total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]

    if total_process_hours <= 0:
        return {"success": False, "error": "修船工序总时长必须大于0，请先配置修船任务"}

    complete, issues = check_tide_data_complete(db, from_date, to_date)
    if not complete:
        return {"success": False, "error": "潮位数据缺失", "issues": issues}

    tides = get_sorted_tides(db, from_date, to_date)
    if len(tides) < 2:
        return {"success": False, "error": "潮位数据不足"}

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
            return {
                "success": True,
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
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

    return {"success": False, "error": "在指定日期范围内未找到满足条件的进出坞时间窗口"}


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
