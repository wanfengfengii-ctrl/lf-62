from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple, Set
from sqlalchemy.orm import Session
from app import models
from collections import defaultdict


ENTER_BUFFER_HOURS = 0.5
EXIT_BUFFER_HOURS = 0.25
MAX_BACKTRACK_ATTEMPTS = 50


def get_material_current_stock(db: Session, material_id: int) -> float:
    latest = (
        db.query(models.MaterialInventory)
        .filter(models.MaterialInventory.material_id == material_id)
        .order_by(models.MaterialInventory.record_time.desc(), models.MaterialInventory.id.desc())
        .first()
    )
    return latest.balance_after if latest else 0.0


def get_ship_total_material_requirements(db: Session, ship_id: int) -> Dict[int, float]:
    tasks = db.query(models.RepairTask).filter(models.RepairTask.ship_id == ship_id).all()
    if not tasks:
        return {}
    task_ids = [t.id for t in tasks]
    reqs = db.query(models.TaskMaterialRequirement).filter(
        models.TaskMaterialRequirement.task_id.in_(task_ids)
    ).all()
    result: Dict[int, float] = defaultdict(float)
    for r in reqs:
        result[r.material_id] += r.quantity
    return dict(result)


def get_ship_total_labor_requirements(db: Session, ship_id: int) -> Dict[str, float]:
    tasks = db.query(models.RepairTask).filter(models.RepairTask.ship_id == ship_id).all()
    if not tasks:
        return {}
    task_ids = [t.id for t in tasks]
    reqs = db.query(models.TaskLaborRequirement).filter(
        models.TaskLaborRequirement.task_id.in_(task_ids)
    ).all()
    result: Dict[str, float] = defaultdict(float)
    for r in reqs:
        result[r.crew_type] += r.required_hours
    return dict(result)


def check_material_sufficiency(
    db: Session,
    material_requirements: Dict[int, float],
    exclude_schedule_ids: Optional[List[int]] = None
) -> Tuple[bool, List[str]]:
    if not material_requirements:
        return True, []
    issues = []
    for material_id, required_qty in material_requirements.items():
        material = db.query(models.Material).filter(models.Material.id == material_id).first()
        if not material:
            issues.append(f"物料ID {material_id} 不存在")
            continue
        current_stock = get_material_current_stock(db, material_id)
        confirmed_qty = get_confirmed_material_quantity(db, material_id, exclude_schedule_ids)
        available = current_stock - confirmed_qty
        if available < required_qty:
            shortage = required_qty - available
            issues.append(
                f"物料[{material.code} {material.name}]不足：需要{required_qty}{material.unit}，"
                f"现有{current_stock}{material.unit}，已被其他排程占用{confirmed_qty}{material.unit}，"
                f"短缺{round(shortage, 2)}{material.unit}"
            )
    return len(issues) == 0, issues


def get_confirmed_material_quantity(
    db: Session,
    material_id: int,
    exclude_schedule_ids: Optional[List[int]] = None
) -> float:
    q = db.query(models.MaterialConsumption).filter(
        models.MaterialConsumption.material_id == material_id
    ).join(models.Schedule).filter(
        models.Schedule.status == "confirmed"
    )
    if exclude_schedule_ids:
        q = q.filter(~models.Schedule.id.in_(exclude_schedule_ids))
    consumptions = q.all()
    return sum(c.actual_quantity if c.actual_quantity is not None else c.planned_quantity for c in consumptions)


def check_labor_sufficiency(
    db: Session,
    labor_requirements: Dict[str, float],
    start_date: date,
    end_date: date,
    exclude_schedule_ids: Optional[List[int]] = None
) -> Tuple[bool, List[str]]:
    if not labor_requirements:
        return True, []
    issues = []
    for crew_type, required_hours in labor_requirements.items():
        crews = db.query(models.Crew).filter(models.Crew.crew_type == crew_type).all()
        if not crews:
            issues.append(f"没有【{crew_type}】班组，需配置相应班组")
            continue
        crew_ids = [c.id for c in crews]
        avail_records = db.query(models.CrewDailyAvailability).filter(
            models.CrewDailyAvailability.crew_id.in_(crew_ids),
            models.CrewDailyAvailability.work_date >= start_date,
            models.CrewDailyAvailability.work_date <= end_date
        ).all()
        total_available = sum(max(0, r.available_hours - r.used_hours) for r in avail_records)
        confirmed_used = get_confirmed_labor_hours(db, crew_type, start_date, end_date, exclude_schedule_ids)
        remaining = total_available - confirmed_used
        if remaining < required_hours:
            shortage = required_hours - remaining
            issues.append(
                f"【{crew_type}】工时不足：需要{required_hours}小时，"
                f"排班可用{total_available}小时，已被其他排程占用{confirmed_used}小时，"
                f"短缺{round(shortage, 2)}小时"
            )
    return len(issues) == 0, issues


def get_confirmed_labor_hours(
    db: Session,
    crew_type: str,
    start_date: date,
    end_date: date,
    exclude_schedule_ids: Optional[List[int]] = None
) -> float:
    confirmed_schedules = db.query(models.Schedule).filter(
        models.Schedule.status == "confirmed",
        models.Schedule.exit_time >= datetime.combine(start_date, datetime.min.time()),
        models.Schedule.enter_time <= datetime.combine(end_date, datetime.max.time())
    )
    if exclude_schedule_ids:
        confirmed_schedules = confirmed_schedules.filter(~models.Schedule.id.in_(exclude_schedule_ids))
    confirmed_schedules = confirmed_schedules.all()
    total = 0.0
    for s in confirmed_schedules:
        ship_id = s.ship_id
        labor_reqs = get_ship_total_labor_requirements(db, ship_id)
        total += labor_reqs.get(crew_type, 0)
    return total


def check_schedule_resources(
    db: Session,
    ship_id: int,
    start_date: date,
    end_date: date,
    exclude_schedule_ids: Optional[List[int]] = None
) -> Tuple[bool, List[str]]:
    all_issues: List[str] = []
    material_reqs = get_ship_total_material_requirements(db, ship_id)
    if material_reqs:
        ok, mat_issues = check_material_sufficiency(db, material_reqs, exclude_schedule_ids)
        if not ok:
            all_issues.extend(mat_issues)
    labor_reqs = get_ship_total_labor_requirements(db, ship_id)
    if labor_reqs:
        ok, labor_issues = check_labor_sufficiency(db, labor_reqs, start_date, end_date, exclude_schedule_ids)
        if not ok:
            all_issues.extend(labor_issues)
    return len(all_issues) == 0, all_issues


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
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None
) -> List[Tuple[datetime, datetime]]:
    windows = []
    if len(tides) < 2:
        return windows

    tide_points = [(parse_tide_time(t.tide_date, t.tide_time), t.water_level) for t in tides]

    if start_dt:
        tide_points = [(t, l) for t, l in tide_points if t >= start_dt]
    if end_dt:
        tide_points = [(t, l) for t, l in tide_points if t <= end_dt]

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
    tides: List[models.Tide],
    prefer_early: bool = True,
    exclude_schedule_ids: Optional[List[int]] = None
) -> Optional[Dict]:
    required_level = max(ship.draft, dock.min_water_level)
    durations = get_process_durations(db, ship.id)
    total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]

    if total_process_hours <= 0:
        return None

    resource_ok, resource_issues = check_schedule_resources(db, ship.id, from_date, to_date, exclude_schedule_ids)
    if not resource_ok:
        return None

    enter_windows = find_water_windows(tides, required_level)
    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    valid_enter_windows = []
    for es, ee in enter_windows:
        if ee < from_dt:
            continue
        if es > to_dt:
            break
        window_start = max(es, from_dt)
        window_end = min(ee, to_dt)
        if window_end > window_start:
            valid_enter_windows.append((window_start, window_end))

    if not prefer_early:
        valid_enter_windows = list(reversed(valid_enter_windows))

    for enter_start, enter_end in valid_enter_windows:
        enter_time = enter_start
        drain_start = enter_time + timedelta(hours=ENTER_BUFFER_HOURS)
        drain_end = drain_start + timedelta(hours=durations["排水"])
        repair_start = drain_end
        repair_end = repair_start + timedelta(hours=durations["修补"])
        oil_start = repair_end if durations["上油"] > 0 else None
        oil_end = oil_start + timedelta(hours=durations["上油"]) if oil_start else repair_end
        earliest_exit = oil_end + timedelta(hours=EXIT_BUFFER_HOURS)

        if earliest_exit > to_dt:
            continue

        exit_windows = find_water_windows(tides, required_level, start_dt=earliest_exit, end_dt=to_dt)

        if exit_windows:
            exit_time = exit_windows[0][0]

            conflict = _check_dock_conflict(dock.id, enter_time, exit_time, existing_schedules)
            if conflict:
                continue

            total_hours = (exit_time - enter_time).total_seconds() / 3600
            dock_utilization = total_process_hours / max(total_hours, 0.001)

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
                "start_drain_time": drain_start,
                "start_repair_time": repair_start,
                "start_oil_time": oil_start,
                "exit_time": exit_time,
                "durations": durations,
                "required_level": required_level,
                "total_hours": total_hours,
                "dock_utilization": dock_utilization
            }

    return None


def _analyze_tide_availability(
    tides: List[models.Tide],
    required_level: float,
    from_date: date,
    to_date: date
) -> Dict:
    enter_windows = find_water_windows(tides, required_level)
    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    valid_enter = []
    for es, ee in enter_windows:
        if ee < from_dt or es > to_dt:
            continue
        window_start = max(es, from_dt)
        window_end = min(ee, to_dt)
        if window_end > window_start:
            valid_enter.append((window_start, window_end))

    total_days = (to_date - from_date).days + 1
    available_hours = sum((e - s).total_seconds() / 3600 for s, e in valid_enter)

    return {
        "has_enter_window": len(valid_enter) > 0,
        "enter_window_count": len(valid_enter),
        "available_hours": round(available_hours, 1),
        "days_with_window": len(set(s.date() for s, _ in valid_enter)),
        "total_days": total_days,
        "windows": valid_enter[:5]
    }


def _build_unassigned_reason(
    ship: models.Ship,
    docks: List[models.Dock],
    tides: List[models.Tide],
    existing_schedules: List[Dict],
    from_date: date,
    to_date: date
) -> str:
    reasons = []
    durations = get_process_durations_via_db(ship.id)
    total_process_hours = durations["排水"] + durations["修补"] + durations["上油"]

    if total_process_hours <= 0:
        return "未配置修船工序（排水+修补+上油总时长为0）"

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        resource_ok, resource_issues = check_schedule_resources(db, ship.id, from_date, to_date)
        if not resource_ok:
            reasons.append("资源校验失败：" + "；".join(resource_issues))
    finally:
        db.close()

    process_detail = f"（排水{durations['排水']}h+修补{durations['修补']}h+上油{durations['上油']}h，共{total_process_hours}h）"

    for dock in docks:
        required_level = max(ship.draft, dock.min_water_level)
        tide_analysis = _analyze_tide_availability(tides, required_level, from_date, to_date)

        if not tide_analysis["has_enter_window"]:
            reasons.append(
                f"船坞{dock.code}：{from_date}~{to_date}共{tide_analysis['total_days']}天内无满足水位≥{required_level}m的进坞窗口"
            )
            continue

        earliest_exit_offset = (
            ENTER_BUFFER_HOURS + durations["排水"] + durations["修补"] +
            (durations["上油"] if durations["上油"] > 0 else 0) + EXIT_BUFFER_HOURS
        )

        has_exit_window = False
        sufficient_window_count = 0
        dock_conflicts = []
        conflict_summary = defaultdict(int)

        for enter_start, enter_end in tide_analysis["windows"]:
            earliest_exit = enter_start + timedelta(hours=earliest_exit_offset)
            if earliest_exit > datetime.combine(to_date, datetime.max.time()):
                continue

            exit_windows = find_water_windows(
                tides, required_level,
                start_dt=earliest_exit,
                end_dt=datetime.combine(to_date, datetime.max.time())
            )

            if exit_windows:
                has_exit_window = True
                sufficient_window_count += 1
                exit_time = exit_windows[0][0]

                conflict = _check_dock_conflict(dock.id, enter_start, exit_time, existing_schedules)
                if conflict:
                    conflict_ship_code = conflict.get("ship_code", "?")
                    conflict_summary[conflict_ship_code] += 1
                    dock_conflicts.append(
                        f"{enter_start.strftime('%m-%d %H:%M')}~{exit_time.strftime('%m-%d %H:%M')}与{conflict_ship_code}冲突"
                    )
                else:
                    dock_conflicts = []
                    break

        if not has_exit_window:
            reasons.append(
                f"船坞{dock.code}：有{tide_analysis['enter_window_count']}个进坞窗口（{tide_analysis['available_hours']}h），但无满足出坞水位≥{required_level}m的窗口（需预留{round(earliest_exit_offset, 1)}h工序时间）"
            )
        elif dock_conflicts:
            conflict_ships = ", ".join([f"{k}×{v}" for k, v in conflict_summary.items()])
            reasons.append(
                f"船坞{dock.code}：有{sufficient_window_count}个可用时段，但均与已排程船只冲突（{conflict_ships}）"
            )
        else:
            reasons.append(f"船坞{dock.code}：潮汐窗口满足，但综合条件不匹配")

    min_dock_level = min(d.min_water_level for d in docks) if docks else 0
    required_level = max(ship.draft, min_dock_level)
    header = f"船只吃水{ship.draft}m，要求水位≥{required_level}m，总工序{total_process_hours}h"

    if len(reasons) == 1:
        return header + "。" + reasons[0]
    else:
        return header + "。各船坞问题：" + "；".join(reasons)


def get_process_durations_via_db(ship_id: int) -> Dict[str, float]:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return get_process_durations(db, ship_id)
    finally:
        db.close()


def _get_dock_utilization_score(
    dock_id: int,
    result: Dict,
    existing_schedules: List[Dict],
    from_date: date,
    to_date: date
) -> float:
    total_span = (datetime.combine(to_date, datetime.max.time()) -
                  datetime.combine(from_date, datetime.min.time())).total_seconds() / 3600

    dock_used_hours = 0
    for s in existing_schedules:
        if s["dock_id"] == dock_id:
            dock_used_hours += (s["exit_time"] - s["enter_time"]).total_seconds() / 3600

    new_hours = (result["exit_time"] - result["enter_time"]).total_seconds() / 3600
    current_utilization = (dock_used_hours + new_hours) / max(total_span, 0.001)

    utilization_score = 1.0 - abs(current_utilization - 0.7)
    process_score = result.get("dock_utilization", 0.8)

    return 0.4 * utilization_score + 0.6 * process_score


def _try_with_backtracking(
    db: Session,
    ships_sorted: List[models.Ship],
    docks: List[models.Dock],
    from_date: date,
    to_date: date,
    tides: List[models.Tide],
    existing_schedules: List[Dict]
) -> Tuple[List[Dict], List[Dict]]:
    scheduled: List[Dict] = []
    unassigned: List[Dict] = []
    temp_scheduled: List[Dict] = []
    working_existing = existing_schedules.copy()

    for idx, ship in enumerate(ships_sorted):
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

        existing_confirmed = db.query(models.Schedule).filter(
            models.Schedule.ship_id == ship.id,
            models.Schedule.status == "confirmed"
        ).first()
        if existing_confirmed:
            dock_code = existing_confirmed.dock.code if existing_confirmed.dock else "?"
            unassigned.append({
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
                "reason": f"该船只已有正式排程（船坞{dock_code}，{existing_confirmed.enter_time.strftime('%Y-%m-%d')}），请先删除原有排程后再试"
            })
            continue

        candidates = []
        for dock in docks:
            result = _try_schedule_ship(db, ship, dock, from_date, to_date, working_existing, tides)
            if result:
                score = _get_dock_utilization_score(dock.id, result, working_existing, from_date, to_date)
                candidates.append((score, result))

        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]["enter_time"]))
            best_result = candidates[0][1]

            temp_scheduled.append(best_result)
            scheduled.append(best_result)
            working_existing.append({
                "dock_id": best_result["dock_id"],
                "enter_time": best_result["enter_time"],
                "exit_time": best_result["exit_time"],
                "ship_id": best_result["ship_id"],
                "ship_code": best_result["ship_code"],
                "dock_code": best_result["dock_code"],
            })
        else:
            backtrack_success = False
            if ship.priority >= 5 and len(temp_scheduled) > 0:
                for attempt in range(min(MAX_BACKTRACK_ATTEMPTS, len(temp_scheduled))):
                    lower_priority_idx = len(temp_scheduled) - 1 - attempt
                    if lower_priority_idx < 0:
                        break

                    lower_item = temp_scheduled[lower_priority_idx]
                    if lower_item["ship_priority"] >= ship.priority:
                        continue

                    removed = temp_scheduled.pop(lower_priority_idx)
                    scheduled.pop(lower_priority_idx)

                    working_existing = [
                        s for s in working_existing
                        if s.get("ship_id") != removed["ship_id"]
                    ]

                    candidates = []
                    for dock in docks:
                        result = _try_schedule_ship(db, ship, dock, from_date, to_date, working_existing, tides)
                        if result:
                            score = _get_dock_utilization_score(dock.id, result, working_existing, from_date, to_date)
                            candidates.append((score, result))

                    if candidates:
                        candidates.sort(key=lambda x: (-x[0], x[1]["enter_time"]))
                        best_result = candidates[0][1]

                        temp_scheduled.append(best_result)
                        scheduled.append(best_result)
                        working_existing.append({
                            "dock_id": best_result["dock_id"],
                            "enter_time": best_result["enter_time"],
                            "exit_time": best_result["exit_time"],
                            "ship_id": best_result["ship_id"],
                            "ship_code": best_result["ship_code"],
                            "dock_code": best_result["dock_code"],
                        })

                        lower_ship = db.query(models.Ship).filter(models.Ship.id == removed["ship_id"]).first()
                        if lower_ship:
                            lower_candidates = []
                            for dock in docks:
                                lower_result = _try_schedule_ship(
                                    db, lower_ship, dock, from_date, to_date, working_existing, tides, prefer_early=False
                                )
                                if lower_result:
                                    lower_score = _get_dock_utilization_score(
                                        dock.id, lower_result, working_existing, from_date, to_date
                                    )
                                    lower_candidates.append((lower_score, lower_result))

                            if lower_candidates:
                                lower_candidates.sort(key=lambda x: (-x[0], x[1]["enter_time"]))
                                lower_best = lower_candidates[0][1]
                                temp_scheduled.append(lower_best)
                                scheduled.append(lower_best)
                                working_existing.append({
                                    "dock_id": lower_best["dock_id"],
                                    "enter_time": lower_best["enter_time"],
                                    "exit_time": lower_best["exit_time"],
                                    "ship_id": lower_best["ship_id"],
                                    "ship_code": lower_best["ship_code"],
                                    "dock_code": lower_best["dock_code"],
                                })
                            else:
                                reason = _build_unassigned_reason(
                                    lower_ship, docks, tides, working_existing, from_date, to_date
                                )
                                unassigned.append({
                                    "ship_id": lower_ship.id,
                                    "ship_code": lower_ship.code,
                                    "ship_name": lower_ship.name,
                                    "reason": reason + f"（为高优先级船只{ship.code}挪出船坞后仍无法安排）"
                                })

                        backtrack_success = True
                        break
                    else:
                        temp_scheduled.insert(lower_priority_idx, removed)
                        scheduled.insert(lower_priority_idx, removed)
                        working_existing.append({
                            "dock_id": removed["dock_id"],
                            "enter_time": removed["enter_time"],
                            "exit_time": removed["exit_time"],
                            "ship_id": removed["ship_id"],
                            "ship_code": removed["ship_code"],
                            "dock_code": removed["dock_code"],
                        })

            if not backtrack_success:
                reason = _build_unassigned_reason(ship, docks, tides, working_existing, from_date, to_date)
                unassigned.append({
                    "ship_id": ship.id,
                    "ship_code": ship.code,
                    "ship_name": ship.name,
                    "reason": reason
                })

    return scheduled, unassigned


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

    tide_start = from_date - timedelta(days=2)
    tide_end = to_date + timedelta(days=3)
    tides = get_sorted_tides(db, tide_start, tide_end)
    if len(tides) < 4:
        complete, issues = check_tide_data_complete(db, from_date, to_date)
        if not complete:
            return {"success": False, "error": "潮位数据缺失", "issues": issues}
        return {"success": False, "error": "潮位数据不足，至少需要4个潮位记录"}

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

    scheduled, unassigned = _try_with_backtracking(
        db, ships_sorted, docks, from_date, to_date, tides, existing_schedules
    )

    scheduled.sort(key=lambda s: (s["enter_time"], s["dock_id"]))
    unassigned.sort(key=lambda u: (-u.get("ship_priority", 0), u["ship_code"]))

    dock_stats = defaultdict(lambda: {"scheduled": 0, "total_hours": 0.0})
    for s in scheduled:
        dock_stats[s["dock_id"]]["scheduled"] += 1
        dock_stats[s["dock_id"]]["total_hours"] += s.get("total_hours", 0)

    return {
        "success": True,
        "scheduled": scheduled,
        "unassigned": unassigned,
        "total_ships": len(ships_sorted),
        "scheduled_count": len(scheduled),
        "unassigned_count": len(unassigned),
        "dock_statistics": dict(dock_stats),
        "algorithm_info": {
            "backtracking_enabled": True,
            "max_backtrack_attempts": MAX_BACKTRACK_ATTEMPTS,
            "scheduling_horizon": f"{from_date.isoformat()} ~ {to_date.isoformat()}",
            "tide_points_used": len(tides)
        }
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


def auto_recalculate_schedules(
    db: Session,
    target_ship_ids: Optional[List[int]] = None,
    target_dock_ids: Optional[List[int]] = None,
    trigger_source: Optional[str] = None
) -> Dict:
    all_draft = db.query(models.Schedule).filter(models.Schedule.status == "draft").all()
    all_conflict = db.query(models.Schedule).filter(models.Schedule.status == "conflict").all()

    affected_draft = all_draft
    affected_conflict = all_conflict

    if target_ship_ids:
        affected_draft = [s for s in affected_draft if s.ship_id in target_ship_ids]
        affected_conflict = [s for s in affected_conflict if s.ship_id in target_ship_ids]
    if target_dock_ids:
        affected_draft = [s for s in affected_draft if s.dock_id in target_dock_ids]
        affected_conflict = [s for s in affected_conflict if s.dock_id in target_dock_ids]

    all_affected = list(set(affected_draft + affected_conflict))

    if not all_affected:
        return {
            "recalculated": 0,
            "failed": 0,
            "unchanged": 0,
            "trigger": trigger_source,
            "message": "无受影响的排程需要重计算"
        }

    ship_ids = list(set(s.ship_id for s in all_affected))
    dock_ids = list(set(s.dock_id for s in all_affected if s.dock_id and s.dock_id > 0))

    if not ship_ids:
        return {
            "recalculated": 0, "failed": 0, "unchanged": 0,
            "trigger": trigger_source,
            "message": "无法确定重计算的船只"
        }

    if not dock_ids:
        all_docks = db.query(models.Dock).all()
        dock_ids = [d.id for d in all_docks]

    min_enter = min(s.enter_time for s in all_affected)
    max_exit = max(s.exit_time for s in all_affected)
    from_date = min_enter.date() - timedelta(days=2)
    to_date = max_exit.date() + timedelta(days=3)

    original_times = {}
    for s in all_affected:
        original_times[s.ship_id] = {
            "enter": s.enter_time,
            "exit": s.exit_time,
            "dock_id": s.dock_id
        }

    for s in all_affected:
        db.delete(s)
    db.flush()

    result = batch_generate_schedule(db, ship_ids, dock_ids, from_date, to_date)

    recalculated = 0
    failed = 0
    unchanged = 0

    for item in result.get("scheduled", []):
        original = original_times.get(item["ship_id"])
        is_unchanged = (original and
                        original["enter"] == item["enter_time"] and
                        original["exit"] == item["exit_time"] and
                        original["dock_id"] == item["dock_id"])

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
        if is_unchanged:
            unchanged += 1
        else:
            recalculated += 1

    for item in result.get("unassigned", []):
        any_dock = db.query(models.Dock).first()
        fallback_dock_id = dock_ids[0] if dock_ids else (any_dock.id if any_dock else 1)
        schedule = models.Schedule(
            ship_id=item["ship_id"],
            dock_id=fallback_dock_id,
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

    return {
        "recalculated": recalculated,
        "failed": failed,
        "unchanged": unchanged,
        "trigger": trigger_source,
        "total_affected": len(all_affected),
        "algorithm_info": result.get("algorithm_info", {})
    }


def get_unscheduled_ships(db: Session) -> List[Dict]:
    all_ships = db.query(models.Ship).order_by(models.Ship.priority.desc(), models.Ship.id).all()
    scheduled_ship_ids: Set[int] = set()
    active_schedules = db.query(models.Schedule).filter(
        models.Schedule.status.in_(["draft", "confirmed"])
    ).all()
    for s in active_schedules:
        scheduled_ship_ids.add(s.ship_id)

    unscheduled = []
    for ship in all_ships:
        if ship.id not in scheduled_ship_ids:
            durations = get_process_durations(db, ship.id)
            total_hours = durations["排水"] + durations["修补"] + durations["上油"]
            has_tasks = total_hours > 0
            conflict = db.query(models.Schedule).filter(
                models.Schedule.ship_id == ship.id,
                models.Schedule.status == "conflict"
            ).first()
            unscheduled.append({
                "ship_id": ship.id,
                "ship_code": ship.code,
                "ship_name": ship.name,
                "draft": ship.draft,
                "priority": ship.priority,
                "has_tasks": has_tasks,
                "conflict_reason": conflict.conflict_reason if conflict else None
            })
    return unscheduled


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
