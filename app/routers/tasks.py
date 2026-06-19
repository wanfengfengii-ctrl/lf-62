from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from app import models
from app.database import get_db

router = APIRouter()


class TaskIn(BaseModel):
    ship_id: int
    process_type: str = Field(..., pattern=r"^(排水|修补|上油)$")
    duration_hours: float = Field(..., gt=0)


class TaskOut(TaskIn):
    id: int

    class Config:
        from_attributes = True


class MaterialRequirementIn(BaseModel):
    task_id: int
    material_id: int
    quantity: float = Field(..., gt=0)


class MaterialRequirementOut(BaseModel):
    id: int
    task_id: int
    material_id: int
    material_code: str
    material_name: str
    category: str
    unit: str
    quantity: float

    class Config:
        from_attributes = True


class LaborRequirementIn(BaseModel):
    task_id: int
    crew_type: str = Field(..., pattern=r"^(木工|油工|杂工|起重|其他)$")
    crew_id: Optional[int] = None
    required_hours: float = Field(..., gt=0)


class LaborRequirementOut(BaseModel):
    id: int
    task_id: int
    crew_type: str
    crew_id: Optional[int]
    crew_code: Optional[str]
    crew_name: Optional[str]
    required_hours: float

    class Config:
        from_attributes = True


class ConsumptionIn(BaseModel):
    schedule_id: int
    material_id: int
    planned_quantity: float = Field(..., gt=0)
    actual_quantity: Optional[float] = None
    operator: Optional[str] = None
    remark: Optional[str] = None


class ConsumptionOut(BaseModel):
    id: int
    schedule_id: int
    ship_code: str
    ship_name: str
    material_id: int
    material_code: str
    material_name: str
    unit: str
    planned_quantity: float
    actual_quantity: Optional[float]
    consumption_time: Optional[str]
    operator: Optional[str]
    remark: Optional[str]

    class Config:
        from_attributes = True


@router.get("", response_model=List[TaskOut])
def list_tasks(db: Session = Depends(get_db)):
    return db.query(models.RepairTask).order_by(models.RepairTask.ship_id, models.RepairTask.process_type).all()


@router.get("/ship/{ship_id}", response_model=List[TaskOut])
def list_tasks_by_ship(ship_id: int, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")
    return db.query(models.RepairTask).filter(models.RepairTask.ship_id == ship_id).all()


@router.post("", response_model=TaskOut)
def create_task(task_in: TaskIn, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == task_in.ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    existing = (
        db.query(models.RepairTask)
        .filter(
            models.RepairTask.ship_id == task_in.ship_id,
            models.RepairTask.process_type == task_in.process_type,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="该船只此工序已配置，请直接修改")

    task = models.RepairTask(**task_in.dict())
    db.add(task)
    db.commit()
    db.refresh(task)

    _invalidate_ship_schedules(db, task_in.ship_id)

    return task


@router.put("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, task_in: TaskIn, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")

    existing = (
        db.query(models.RepairTask)
        .filter(
            models.RepairTask.ship_id == task_in.ship_id,
            models.RepairTask.process_type == task_in.process_type,
            models.RepairTask.id != task_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="该船只此工序已存在")

    old_ship_id = task.ship_id
    for key, value in task_in.dict().items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)

    _invalidate_ship_schedules(db, old_ship_id)
    if old_ship_id != task_in.ship_id:
        _invalidate_ship_schedules(db, task_in.ship_id)

    return task


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")
    ship_id = task.ship_id
    db.delete(task)
    db.commit()

    _invalidate_ship_schedules(db, ship_id)

    return {"ok": True}


@router.get("/{task_id}/material-requirements", response_model=List[MaterialRequirementOut])
def list_task_material_requirements(task_id: int, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")
    reqs = db.query(models.TaskMaterialRequirement).filter(models.TaskMaterialRequirement.task_id == task_id).all()
    result = []
    for r in reqs:
        m = r.material
        result.append({
            "id": r.id,
            "task_id": r.task_id,
            "material_id": r.material_id,
            "material_code": m.code if m else "",
            "material_name": m.name if m else "",
            "category": m.category if m else "",
            "unit": m.unit if m else "",
            "quantity": r.quantity
        })
    return result


@router.post("/material-requirements", response_model=MaterialRequirementOut)
def create_task_material_requirement(req_in: MaterialRequirementIn, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == req_in.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")
    material = db.query(models.Material).filter(models.Material.id == req_in.material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="物料不存在")
    existing = db.query(models.TaskMaterialRequirement).filter(
        models.TaskMaterialRequirement.task_id == req_in.task_id,
        models.TaskMaterialRequirement.material_id == req_in.material_id
    ).first()
    if existing:
        existing.quantity = req_in.quantity
        db.commit()
        db.refresh(existing)
        _invalidate_ship_schedules(db, task.ship_id)
        return {
            "id": existing.id,
            "task_id": existing.task_id,
            "material_id": existing.material_id,
            "material_code": material.code,
            "material_name": material.name,
            "category": material.category,
            "unit": material.unit,
            "quantity": existing.quantity
        }
    req = models.TaskMaterialRequirement(**req_in.dict())
    db.add(req)
    db.commit()
    db.refresh(req)
    _invalidate_ship_schedules(db, task.ship_id)
    return {
        "id": req.id,
        "task_id": req.task_id,
        "material_id": req.material_id,
        "material_code": material.code,
        "material_name": material.name,
        "category": material.category,
        "unit": material.unit,
        "quantity": req.quantity
    }


@router.delete("/material-requirements/{req_id}")
def delete_task_material_requirement(req_id: int, db: Session = Depends(get_db)):
    req = db.query(models.TaskMaterialRequirement).filter(models.TaskMaterialRequirement.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="物料需求不存在")
    task = req.task
    ship_id = task.ship_id if task else None
    db.delete(req)
    db.commit()
    if ship_id:
        _invalidate_ship_schedules(db, ship_id)
    return {"ok": True}


@router.get("/{task_id}/labor-requirements", response_model=List[LaborRequirementOut])
def list_task_labor_requirements(task_id: int, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")
    reqs = db.query(models.TaskLaborRequirement).filter(models.TaskLaborRequirement.task_id == task_id).all()
    result = []
    for r in reqs:
        c = r.crew
        result.append({
            "id": r.id,
            "task_id": r.task_id,
            "crew_type": r.crew_type,
            "crew_id": r.crew_id,
            "crew_code": c.code if c else None,
            "crew_name": c.name if c else None,
            "required_hours": r.required_hours
        })
    return result


@router.post("/labor-requirements", response_model=LaborRequirementOut)
def create_task_labor_requirement(req_in: LaborRequirementIn, db: Session = Depends(get_db)):
    task = db.query(models.RepairTask).filter(models.RepairTask.id == req_in.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="修船任务不存在")
    crew = None
    if req_in.crew_id:
        crew = db.query(models.Crew).filter(models.Crew.id == req_in.crew_id).first()
        if not crew:
            raise HTTPException(status_code=404, detail="班组不存在")
    existing = db.query(models.TaskLaborRequirement).filter(
        models.TaskLaborRequirement.task_id == req_in.task_id,
        models.TaskLaborRequirement.crew_type == req_in.crew_type
    ).first()
    if existing:
        existing.crew_id = req_in.crew_id
        existing.required_hours = req_in.required_hours
        db.commit()
        db.refresh(existing)
        _invalidate_ship_schedules(db, task.ship_id)
        crew = existing.crew
        return {
            "id": existing.id,
            "task_id": existing.task_id,
            "crew_type": existing.crew_type,
            "crew_id": existing.crew_id,
            "crew_code": crew.code if crew else None,
            "crew_name": crew.name if crew else None,
            "required_hours": existing.required_hours
        }
    req = models.TaskLaborRequirement(**req_in.dict())
    db.add(req)
    db.commit()
    db.refresh(req)
    _invalidate_ship_schedules(db, task.ship_id)
    crew = req.crew
    return {
        "id": req.id,
        "task_id": req.task_id,
        "crew_type": req.crew_type,
        "crew_id": req.crew_id,
        "crew_code": crew.code if crew else None,
        "crew_name": crew.name if crew else None,
        "required_hours": req.required_hours
    }


@router.delete("/labor-requirements/{req_id}")
def delete_task_labor_requirement(req_id: int, db: Session = Depends(get_db)):
    req = db.query(models.TaskLaborRequirement).filter(models.TaskLaborRequirement.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="工时需求不存在")
    task = req.task
    ship_id = task.ship_id if task else None
    db.delete(req)
    db.commit()
    if ship_id:
        _invalidate_ship_schedules(db, ship_id)
    return {"ok": True}


@router.get("/consumptions", response_model=List[ConsumptionOut])
def list_consumptions(schedule_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.MaterialConsumption)
    if schedule_id:
        q = q.filter(models.MaterialConsumption.schedule_id == schedule_id)
    consumptions = q.order_by(models.MaterialConsumption.id.desc()).all()
    result = []
    for c in consumptions:
        schedule = c.schedule
        ship = schedule.ship if schedule else None
        material = c.material
        result.append({
            "id": c.id,
            "schedule_id": c.schedule_id,
            "ship_code": ship.code if ship else "",
            "ship_name": ship.name if ship else "",
            "material_id": c.material_id,
            "material_code": material.code if material else "",
            "material_name": material.name if material else "",
            "unit": material.unit if material else "",
            "planned_quantity": c.planned_quantity,
            "actual_quantity": c.actual_quantity,
            "consumption_time": c.consumption_time.isoformat() if c.consumption_time else None,
            "operator": c.operator,
            "remark": c.remark
        })
    return result


def _invalidate_ship_schedules(db: Session, ship_id: int):
    from app.scheduler import auto_recalculate_schedules
    auto_recalculate_schedules(
        db,
        target_ship_ids=[ship_id],
        trigger_source=f"task_config_change:ship_{ship_id}"
    )
    from app.routers.costs import recalculate_ship_costs_and_quotations
    recalculate_ship_costs_and_quotations(db, [ship_id])
