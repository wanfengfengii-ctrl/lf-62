from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
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


def _invalidate_ship_schedules(db: Session, ship_id: int):
    from app.scheduler import auto_recalculate_schedules
    auto_recalculate_schedules(
        db,
        target_ship_ids=[ship_id],
        trigger_source=f"task_config_change:ship_{ship_id}"
    )
