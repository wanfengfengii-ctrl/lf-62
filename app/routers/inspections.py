from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, date
from app import models
from app.database import get_db
import os
import uuid

router = APIRouter()


def _generate_no(prefix: str, db: Session, model_cls, field: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.query(model_cls).filter(
        getattr(model_cls, field).like(f"{prefix}{today}%")
    ).count() + 1
    return f"{prefix}{today}{count:03d}"


STAGE_TIME_MAP = {
    "进坞": "enter_time",
    "排水": "start_drain_time",
    "修补": "start_repair_time",
    "上油": "start_oil_time",
    "出坞": "exit_time"
}


def _sync_tasks_for_schedule(db: Session, schedule: models.Schedule):
    existing_tasks = db.query(models.InspectionTask).filter(
        models.InspectionTask.schedule_id == schedule.id
    ).all()
    existing_stages = {t.stage: t for t in existing_tasks}

    for stage in models.INSPECTION_STAGES:
        time_field = STAGE_TIME_MAP.get(stage)
        planned_time = None
        if time_field and hasattr(schedule, time_field):
            planned_time = getattr(schedule, time_field)
        if stage in existing_stages:
            task = existing_stages[stage]
            task.ship_id = schedule.ship_id
            task.dock_id = schedule.dock_id
            task.planned_time = planned_time
        else:
            task_no = _generate_no("IT", db, models.InspectionTask, "task_no")
            task = models.InspectionTask(
                task_no=task_no,
                schedule_id=schedule.id,
                ship_id=schedule.ship_id,
                dock_id=schedule.dock_id,
                stage=stage,
                planned_time=planned_time,
                status="pending"
            )
            db.add(task)


def check_ship_high_risk_unrectified(db: Session, ship_id: int) -> List[str]:
    hazards = db.query(models.Hazard).filter(
        models.Hazard.ship_id == ship_id,
        models.Hazard.hazard_level.in_(["较大风险", "重大风险"]),
        models.Hazard.status.notin_(["closed", "rectified"])
    ).all()
    return [f"{h.hazard_no}-{h.title}" for h in hazards]


class StandardIn(BaseModel):
    code: Optional[str] = None
    name: str
    stage: str
    category: Optional[str] = None
    content: str
    standard_value: Optional[str] = None
    method: Optional[str] = None
    is_active: bool = True
    remark: Optional[str] = None


class StandardOut(BaseModel):
    id: int
    code: str
    name: str
    stage: str
    category: Optional[str]
    content: str
    standard_value: Optional[str]
    method: Optional[str]
    is_active: bool
    remark: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/standards", response_model=List[StandardOut])
def list_standards(
    stage: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.InspectionStandard)
    if stage:
        q = q.filter(models.InspectionStandard.stage == stage)
    if is_active is not None:
        q = q.filter(models.InspectionStandard.is_active == is_active)
    return q.order_by(models.InspectionStandard.stage, models.InspectionStandard.code).all()


@router.get("/standards/{sid}", response_model=StandardOut)
def get_standard(sid: int, db: Session = Depends(get_db)):
    s = db.query(models.InspectionStandard).filter(models.InspectionStandard.id == sid).first()
    if not s:
        raise HTTPException(404, "标准不存在")
    return s


@router.post("/standards", response_model=StandardOut)
def create_standard(data: StandardIn, db: Session = Depends(get_db)):
    if data.stage not in models.INSPECTION_STAGES:
        raise HTTPException(400, f"阶段必须是: {', '.join(models.INSPECTION_STAGES)}")
    code = data.code or _generate_no("IS", db, models.InspectionStandard, "code")
    s = models.InspectionStandard(**data.dict(exclude={"code"}), code=code)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.put("/standards/{sid}", response_model=StandardOut)
def update_standard(sid: int, data: StandardIn, db: Session = Depends(get_db)):
    s = db.query(models.InspectionStandard).filter(models.InspectionStandard.id == sid).first()
    if not s:
        raise HTTPException(404, "标准不存在")
    for k, v in data.dict(exclude_unset=True).items():
        if v is not None or k == "is_active":
            setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/standards/{sid}")
def delete_standard(sid: int, db: Session = Depends(get_db)):
    s = db.query(models.InspectionStandard).filter(models.InspectionStandard.id == sid).first()
    if not s:
        raise HTTPException(404, "标准不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


class RiskItemIn(BaseModel):
    standard_id: int
    code: Optional[str] = None
    name: str
    description: Optional[str] = None
    default_hazard_level: str = "一般风险"
    consequence: Optional[str] = None
    is_active: bool = True
    remark: Optional[str] = None


class RiskItemOut(BaseModel):
    id: int
    standard_id: int
    standard_name: Optional[str]
    code: str
    name: str
    description: Optional[str]
    default_hazard_level: str
    consequence: Optional[str]
    is_active: bool
    remark: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _risk_to_dict(r: models.RiskItem) -> dict:
    return {
        "id": r.id,
        "standard_id": r.standard_id,
        "standard_name": r.standard.name if r.standard else None,
        "code": r.code,
        "name": r.name,
        "description": r.description,
        "default_hazard_level": r.default_hazard_level,
        "consequence": r.consequence,
        "is_active": r.is_active,
        "remark": r.remark,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@router.get("/risk-items")
def list_risk_items(
    standard_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.RiskItem)
    if standard_id:
        q = q.filter(models.RiskItem.standard_id == standard_id)
    if is_active is not None:
        q = q.filter(models.RiskItem.is_active == is_active)
    items = q.order_by(models.RiskItem.code).all()
    return [_risk_to_dict(r) for r in items]


@router.post("/risk-items")
def create_risk_item(data: RiskItemIn, db: Session = Depends(get_db)):
    std = db.query(models.InspectionStandard).filter(models.InspectionStandard.id == data.standard_id).first()
    if not std:
        raise HTTPException(400, "巡检标准不存在")
    if data.default_hazard_level not in models.HAZARD_LEVELS:
        raise HTTPException(400, f"风险等级必须是: {', '.join(models.HAZARD_LEVELS)}")
    code = data.code or _generate_no("RI", db, models.RiskItem, "code")
    r = models.RiskItem(**data.dict(exclude={"code"}), code=code)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _risk_to_dict(r)


@router.put("/risk-items/{rid}")
def update_risk_item(rid: int, data: RiskItemIn, db: Session = Depends(get_db)):
    r = db.query(models.RiskItem).filter(models.RiskItem.id == rid).first()
    if not r:
        raise HTTPException(404, "风险项不存在")
    for k, v in data.dict(exclude_unset=True).items():
        if v is not None or k == "is_active":
            setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _risk_to_dict(r)


@router.delete("/risk-items/{rid}")
def delete_risk_item(rid: int, db: Session = Depends(get_db)):
    r = db.query(models.RiskItem).filter(models.RiskItem.id == rid).first()
    if not r:
        raise HTTPException(404, "风险项不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


class ResponsiblePersonIn(BaseModel):
    code: Optional[str] = None
    name: str
    position: Optional[str] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_active: bool = True
    remark: Optional[str] = None


class ResponsiblePersonOut(BaseModel):
    id: int
    code: str
    name: str
    position: Optional[str]
    department: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    is_active: bool
    remark: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/responsible-persons", response_model=List[ResponsiblePersonOut])
def list_responsible_persons(
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.ResponsiblePerson)
    if is_active is not None:
        q = q.filter(models.ResponsiblePerson.is_active == is_active)
    return q.order_by(models.ResponsiblePerson.code).all()


@router.post("/responsible-persons", response_model=ResponsiblePersonOut)
def create_responsible_person(data: ResponsiblePersonIn, db: Session = Depends(get_db)):
    code = data.code or _generate_no("RP", db, models.ResponsiblePerson, "code")
    r = models.ResponsiblePerson(**data.dict(exclude={"code"}), code=code)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.put("/responsible-persons/{rid}", response_model=ResponsiblePersonOut)
def update_responsible_person(rid: int, data: ResponsiblePersonIn, db: Session = Depends(get_db)):
    r = db.query(models.ResponsiblePerson).filter(models.ResponsiblePerson.id == rid).first()
    if not r:
        raise HTTPException(404, "责任人不存在")
    for k, v in data.dict(exclude_unset=True).items():
        if v is not None or k == "is_active":
            setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return r


@router.delete("/responsible-persons/{rid}")
def delete_responsible_person(rid: int, db: Session = Depends(get_db)):
    r = db.query(models.ResponsiblePerson).filter(models.ResponsiblePerson.id == rid).first()
    if not r:
        raise HTTPException(404, "责任人不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


def _task_to_dict(t: models.InspectionTask) -> dict:
    return {
        "id": t.id,
        "task_no": t.task_no,
        "schedule_id": t.schedule_id,
        "ship_id": t.ship_id,
        "ship_code": t.ship.code if t.ship else "",
        "ship_name": t.ship.name if t.ship else "",
        "dock_id": t.dock_id,
        "dock_code": t.dock.code if t.dock else "",
        "dock_name": t.dock.name if t.dock else "",
        "stage": t.stage,
        "planned_time": t.planned_time,
        "actual_start_time": t.actual_start_time,
        "actual_end_time": t.actual_end_time,
        "inspector": t.inspector,
        "status": t.status,
        "remark": t.remark,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "record_count": len(t.records),
        "hazard_count": sum(len(r.hazards) for r in t.records),
    }


@router.get("/tasks")
def list_tasks(
    status: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    ship_id: Optional[int] = Query(None),
    dock_id: Optional[int] = Query(None),
    schedule_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.InspectionTask)
    if status:
        q = q.filter(models.InspectionTask.status == status)
    if stage:
        q = q.filter(models.InspectionTask.stage == stage)
    if ship_id:
        q = q.filter(models.InspectionTask.ship_id == ship_id)
    if dock_id:
        q = q.filter(models.InspectionTask.dock_id == dock_id)
    if schedule_id:
        q = q.filter(models.InspectionTask.schedule_id == schedule_id)
    tasks = q.order_by(models.InspectionTask.planned_time.desc().nullslast()).all()
    return [_task_to_dict(t) for t in tasks]


@router.get("/tasks/{tid}")
def get_task(tid: int, db: Session = Depends(get_db)):
    t = db.query(models.InspectionTask).filter(models.InspectionTask.id == tid).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    d = _task_to_dict(t)
    d["records"] = [_record_to_dict(r) for r in t.records]
    standards = db.query(models.InspectionStandard).filter(
        models.InspectionStandard.stage == t.stage,
        models.InspectionStandard.is_active == True
    ).all()
    d["standards"] = [
        {"id": s.id, "code": s.code, "name": s.name, "content": s.content,
         "standard_value": s.standard_value, "method": s.method}
        for s in standards
    ]
    return d


@router.post("/tasks/generate-for-schedule/{schedule_id}")
def generate_tasks_for_schedule(schedule_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if not s:
        raise HTTPException(404, "排程不存在")
    _sync_tasks_for_schedule(db, s)
    db.commit()
    tasks = db.query(models.InspectionTask).filter(
        models.InspectionTask.schedule_id == schedule_id
    ).all()
    return {"generated": len(tasks), "tasks": [_task_to_dict(t) for t in tasks]}


@router.post("/tasks/{tid}/start")
def start_task(tid: int, db: Session = Depends(get_db)):
    t = db.query(models.InspectionTask).filter(models.InspectionTask.id == tid).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    t.status = "in_progress"
    t.actual_start_time = datetime.now()
    db.commit()
    return {"ok": True}


@router.post("/tasks/{tid}/complete")
def complete_task(tid: int, db: Session = Depends(get_db)):
    t = db.query(models.InspectionTask).filter(models.InspectionTask.id == tid).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    t.status = "completed"
    t.actual_end_time = datetime.now()
    db.commit()
    return {"ok": True}


@router.delete("/tasks/{tid}")
def delete_task(tid: int, db: Session = Depends(get_db)):
    t = db.query(models.InspectionTask).filter(models.InspectionTask.id == tid).first()
    if not t:
        raise HTTPException(404, "任务不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}


class RecordItemIn(BaseModel):
    standard_id: int
    check_result: str
    actual_value: Optional[str] = None
    is_conforming: bool = True
    remark: Optional[str] = None


class HazardIn(BaseModel):
    title: str
    description: str
    hazard_level: str = "一般风险"
    risk_item_id: Optional[int] = None
    location: Optional[str] = None
    responsible_person_id: Optional[int] = None
    deadline: Optional[date] = None
    rectification_measure: Optional[str] = None


class InspectionRecordIn(BaseModel):
    task_id: int
    inspector: Optional[str] = None
    weather: Optional[str] = None
    temperature: Optional[str] = None
    overall_result: Optional[str] = None
    remark: Optional[str] = None
    items: List[RecordItemIn] = []
    hazards: List[HazardIn] = []


def _record_to_dict(r: models.InspectionRecord) -> dict:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "record_time": r.record_time,
        "inspector": r.inspector,
        "weather": r.weather,
        "temperature": r.temperature,
        "overall_result": r.overall_result,
        "remark": r.remark,
        "items": [
            {
                "id": it.id,
                "standard_id": it.standard_id,
                "standard_code": it.standard.code if it.standard else "",
                "standard_name": it.standard.name if it.standard else "",
                "check_result": it.check_result,
                "actual_value": it.actual_value,
                "is_conforming": it.is_conforming,
                "remark": it.remark,
            } for it in r.items
        ],
        "photos": [
            {"id": p.id, "file_name": p.file_name, "file_path": p.file_path,
             "file_size": p.file_size, "description": p.description, "uploaded_at": p.uploaded_at}
            for p in r.photos
        ],
        "hazards": [_hazard_to_dict(h) for h in r.hazards],
    }


def _hazard_to_dict(h: models.Hazard) -> dict:
    return {
        "id": h.id,
        "hazard_no": h.hazard_no,
        "record_id": h.record_id,
        "task_id": h.task_id,
        "schedule_id": h.schedule_id,
        "ship_id": h.ship_id,
        "ship_code": h.ship.code if h.ship else "",
        "ship_name": h.ship.name if h.ship else "",
        "dock_id": h.dock_id,
        "dock_code": h.dock.code if h.dock else "",
        "risk_item_id": h.risk_item_id,
        "risk_item_name": h.risk_item.name if h.risk_item else "",
        "stage": h.stage,
        "title": h.title,
        "description": h.description,
        "hazard_level": h.hazard_level,
        "location": h.location,
        "responsible_person_id": h.responsible_person_id,
        "responsible_person_name": h.responsible_person.name if h.responsible_person else "",
        "responsible_person_phone": h.responsible_person.phone if h.responsible_person else "",
        "status": h.status,
        "discovered_time": h.discovered_time,
        "deadline": h.deadline,
        "rectified_time": h.rectified_time,
        "closed_time": h.closed_time,
        "rectification_measure": h.rectification_measure,
        "rectification_result": h.rectification_result,
        "remark": h.remark,
        "created_at": h.created_at,
        "updated_at": h.updated_at,
        "is_overdue": h.deadline is not None and h.deadline < date.today() and h.status not in ["closed", "rectified"],
        "rectification_records": [
            {"id": rr.id, "action": rr.action, "description": rr.description,
             "operator": rr.operator, "record_time": rr.record_time, "remark": rr.remark}
            for rr in h.rectification_records
        ],
    }


@router.post("/records")
def create_record(data: InspectionRecordIn, db: Session = Depends(get_db)):
    task = db.query(models.InspectionTask).filter(models.InspectionTask.id == data.task_id).first()
    if not task:
        raise HTTPException(404, "巡检任务不存在")

    r = models.InspectionRecord(
        task_id=data.task_id,
        inspector=data.inspector,
        weather=data.weather,
        temperature=data.temperature,
        overall_result=data.overall_result,
        remark=data.remark,
    )
    db.add(r)
    db.flush()

    for item in data.items:
        it = models.InspectionRecordItem(
            record_id=r.id,
            standard_id=item.standard_id,
            check_result=item.check_result,
            actual_value=item.actual_value,
            is_conforming=item.is_conforming,
            remark=item.remark,
        )
        db.add(it)

    for hz in data.hazards:
        if hz.hazard_level not in models.HAZARD_LEVELS:
            raise HTTPException(400, f"风险等级必须是: {', '.join(models.HAZARD_LEVELS)}")
        hazard_no = _generate_no("HZ", db, models.Hazard, "hazard_no")
        h = models.Hazard(
            hazard_no=hazard_no,
            record_id=r.id,
            task_id=task.id,
            schedule_id=task.schedule_id,
            ship_id=task.ship_id,
            dock_id=task.dock_id,
            risk_item_id=hz.risk_item_id,
            stage=task.stage,
            title=hz.title,
            description=hz.description,
            hazard_level=hz.hazard_level,
            location=hz.location,
            responsible_person_id=hz.responsible_person_id,
            deadline=hz.deadline,
            rectification_measure=hz.rectification_measure,
            status="open",
        )
        db.add(h)

    if task.status == "pending":
        task.status = "in_progress"
        task.actual_start_time = datetime.now()

    db.commit()
    db.refresh(r)
    return _record_to_dict(r)


@router.get("/records")
def list_records(
    task_id: Optional[int] = Query(None),
    ship_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.InspectionRecord)
    if task_id:
        q = q.filter(models.InspectionRecord.task_id == task_id)
    if ship_id:
        q = q.join(models.InspectionTask).filter(models.InspectionTask.ship_id == ship_id)
    records = q.order_by(models.InspectionRecord.record_time.desc()).all()
    return [_record_to_dict(r) for r in records]


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/photos/{record_id}")
async def upload_photo(record_id: int, description: Optional[str] = "", file: UploadFile = File(...), db: Session = Depends(get_db)):
    r = db.query(models.InspectionRecord).filter(models.InspectionRecord.id == record_id).first()
    if not r:
        raise HTTPException(404, "巡检记录不存在")
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    new_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, new_name)
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    p = models.InspectionPhoto(
        record_id=record_id,
        file_name=file.filename or new_name,
        file_path=f"/uploads/{new_name}",
        file_size=len(content),
        description=description,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "file_name": p.file_name, "file_path": p.file_path, "file_size": p.file_size}


@router.get("/hazards")
def list_hazards(
    status: Optional[str] = Query(None),
    hazard_level: Optional[str] = Query(None),
    ship_id: Optional[int] = Query(None),
    dock_id: Optional[int] = Query(None),
    stage: Optional[str] = Query(None),
    only_overdue: bool = Query(False),
    db: Session = Depends(get_db)
):
    q = db.query(models.Hazard)
    if status:
        q = q.filter(models.Hazard.status == status)
    if hazard_level:
        q = q.filter(models.Hazard.hazard_level == hazard_level)
    if ship_id:
        q = q.filter(models.Hazard.ship_id == ship_id)
    if dock_id:
        q = q.filter(models.Hazard.dock_id == dock_id)
    if stage:
        q = q.filter(models.Hazard.stage == stage)
    if only_overdue:
        q = q.filter(
            models.Hazard.deadline.isnot(None),
            models.Hazard.deadline < date.today(),
            models.Hazard.status.notin_(["closed", "rectified"])
        )
    hazards = q.order_by(models.Hazard.created_at.desc()).all()
    return [_hazard_to_dict(h) for h in hazards]


@router.get("/hazards/{hid}")
def get_hazard(hid: int, db: Session = Depends(get_db)):
    h = db.query(models.Hazard).filter(models.Hazard.id == hid).first()
    if not h:
        raise HTTPException(404, "隐患不存在")
    return _hazard_to_dict(h)


class HazardUpdateIn(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    hazard_level: Optional[str] = None
    location: Optional[str] = None
    responsible_person_id: Optional[int] = None
    status: Optional[str] = None
    deadline: Optional[date] = None
    rectification_measure: Optional[str] = None
    rectification_result: Optional[str] = None
    remark: Optional[str] = None


@router.put("/hazards/{hid}")
def update_hazard(hid: int, data: HazardUpdateIn, db: Session = Depends(get_db)):
    h = db.query(models.Hazard).filter(models.Hazard.id == hid).first()
    if not h:
        raise HTTPException(404, "隐患不存在")
    for k, v in data.dict(exclude_unset=True).items():
        if v is not None:
            setattr(h, k, v)
    if data.status == "rectified" and not h.rectified_time:
        h.rectified_time = datetime.now()
    if data.status == "closed" and not h.closed_time:
        h.closed_time = datetime.now()
    db.commit()
    db.refresh(h)
    return _hazard_to_dict(h)


class RectificationRecordIn(BaseModel):
    hazard_id: int
    action: str
    description: str
    operator: Optional[str] = None
    remark: Optional[str] = None


@router.post("/rectification-records")
def create_rectification_record(data: RectificationRecordIn, db: Session = Depends(get_db)):
    h = db.query(models.Hazard).filter(models.Hazard.id == data.hazard_id).first()
    if not h:
        raise HTTPException(404, "隐患不存在")
    rr = models.RectificationRecord(**data.dict())
    db.add(rr)
    if h.status == "open":
        h.status = "rectifying"
    db.commit()
    db.refresh(rr)
    return {
        "id": rr.id, "hazard_id": rr.hazard_id, "action": rr.action,
        "description": rr.description, "operator": rr.operator,
        "record_time": rr.record_time, "remark": rr.remark
    }


@router.get("/statistics")
def get_statistics(
    ship_id: Optional[int] = Query(None),
    dock_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    today = date.today()
    hazard_q = db.query(models.Hazard)
    if ship_id:
        hazard_q = hazard_q.filter(models.Hazard.ship_id == ship_id)
    if dock_id:
        hazard_q = hazard_q.filter(models.Hazard.dock_id == dock_id)
    all_hazards = hazard_q.all()

    by_level = {lv: 0 for lv in models.HAZARD_LEVELS}
    by_status = {st: 0 for st in models.HAZARD_STATUS}
    by_stage = {s: 0 for s in models.INSPECTION_STAGES}
    overdue_count = 0
    high_risk_open = 0

    for h in all_hazards:
        by_level[h.hazard_level] = by_level.get(h.hazard_level, 0) + 1
        by_status[h.status] = by_status.get(h.status, 0) + 1
        if h.stage:
            by_stage[h.stage] = by_stage.get(h.stage, 0) + 1
        if h.deadline and h.deadline < today and h.status not in ["closed", "rectified"]:
            overdue_count += 1
        if h.hazard_level in ["较大风险", "重大风险"] and h.status not in ["closed", "rectified"]:
            high_risk_open += 1

    task_q = db.query(models.InspectionTask)
    if ship_id:
        task_q = task_q.filter(models.InspectionTask.ship_id == ship_id)
    if dock_id:
        task_q = task_q.filter(models.InspectionTask.dock_id == dock_id)
    tasks = task_q.all()
    task_by_status = {st: 0 for st in models.INSPECTION_STATUS}
    task_by_stage = {s: 0 for s in models.INSPECTION_STAGES}
    for t in tasks:
        task_by_status[t.status] = task_by_status.get(t.status, 0) + 1
        task_by_stage[t.stage] = task_by_stage.get(t.stage, 0) + 1

    return {
        "total_hazards": len(all_hazards),
        "by_level": by_level,
        "by_status": by_status,
        "by_stage": by_stage,
        "overdue_count": overdue_count,
        "high_risk_open": high_risk_open,
        "total_tasks": len(tasks),
        "task_by_status": task_by_status,
        "task_by_stage": task_by_stage,
    }


@router.get("/stages")
def get_stages():
    return models.INSPECTION_STAGES


@router.get("/hazard-levels")
def get_hazard_levels():
    return models.HAZARD_LEVELS


@router.get("/task-statuses")
def get_task_statuses():
    return models.INSPECTION_STATUS


@router.get("/hazard-statuses")
def get_hazard_statuses():
    return models.HAZARD_STATUS
