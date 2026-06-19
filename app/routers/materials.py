from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, date
from app import models
from app.database import get_db

router = APIRouter()


class MaterialIn(BaseModel):
    code: str
    name: str
    category: str = Field(..., pattern=r"^(木材|油料|绳索|铁件|其他)$")
    unit: str
    safety_stock: float = Field(..., ge=0)
    description: Optional[str] = None


class MaterialOut(MaterialIn):
    id: int
    current_stock: Optional[float] = None

    class Config:
        from_attributes = True


class InventoryIn(BaseModel):
    material_id: int
    quantity: float
    record_type: str = Field(..., pattern=r"^(入库|出库|盘点调整)$")
    reference_no: Optional[str] = None
    operator: Optional[str] = None
    remark: Optional[str] = None


class InventoryOut(BaseModel):
    id: int
    material_id: int
    material_code: str
    material_name: str
    quantity: float
    record_type: str
    reference_no: Optional[str]
    operator: Optional[str]
    remark: Optional[str]
    record_time: datetime
    balance_after: float

    class Config:
        from_attributes = True


def get_material_current_stock(db: Session, material_id: int) -> float:
    latest = (
        db.query(models.MaterialInventory)
        .filter(models.MaterialInventory.material_id == material_id)
        .order_by(models.MaterialInventory.record_time.desc(), models.MaterialInventory.id.desc())
        .first()
    )
    return latest.balance_after if latest else 0.0


def _material_with_stock(db: Session, m: models.Material) -> dict:
    return {
        "id": m.id,
        "code": m.code,
        "name": m.name,
        "category": m.category,
        "unit": m.unit,
        "safety_stock": m.safety_stock,
        "description": m.description,
        "current_stock": get_material_current_stock(db, m.id)
    }


@router.get("", response_model=List[MaterialOut])
def list_materials(
    category: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    low_stock_only: bool = Query(False),
    db: Session = Depends(get_db)
):
    q = db.query(models.Material)
    if category:
        q = q.filter(models.Material.category == category)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((models.Material.code.like(like)) | (models.Material.name.like(like)))
    materials = q.order_by(models.Material.code).all()
    result = [_material_with_stock(db, m) for m in materials]
    if low_stock_only:
        result = [r for r in result if r["current_stock"] <= r["safety_stock"]]
    return result


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, db: Session = Depends(get_db)):
    m = db.query(models.Material).filter(models.Material.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="物料不存在")
    return _material_with_stock(db, m)


@router.post("", response_model=MaterialOut)
def create_material(material_in: MaterialIn, db: Session = Depends(get_db)):
    existing = db.query(models.Material).filter(models.Material.code == material_in.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="物料编码已存在")
    m = models.Material(**material_in.dict())
    db.add(m)
    db.flush()
    db.commit()
    return _material_with_stock(db, m)


@router.put("/{material_id}", response_model=MaterialOut)
def update_material(material_id: int, material_in: MaterialIn, db: Session = Depends(get_db)):
    m = db.query(models.Material).filter(models.Material.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="物料不存在")
    existing = db.query(models.Material).filter(
        models.Material.code == material_in.code,
        models.Material.id != material_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="物料编码已存在")
    for key, value in material_in.dict().items():
        setattr(m, key, value)
    db.commit()
    db.refresh(m)
    return _material_with_stock(db, m)


@router.delete("/{material_id}")
def delete_material(material_id: int, db: Session = Depends(get_db)):
    m = db.query(models.Material).filter(models.Material.id == material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="物料不存在")
    if m.inventory_records:
        raise HTTPException(status_code=400, detail="该物料已有库存记录，不能删除")
    if m.task_requirements:
        raise HTTPException(status_code=400, detail="该物料已被任务引用，不能删除")
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.get("/inventory/logs", response_model=List[InventoryOut])
def list_inventory_logs(
    material_id: Optional[int] = Query(None),
    record_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(models.MaterialInventory)
    if material_id:
        q = q.filter(models.MaterialInventory.material_id == material_id)
    if record_type:
        q = q.filter(models.MaterialInventory.record_type == record_type)
    if from_date:
        q = q.filter(models.MaterialInventory.record_time >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.filter(models.MaterialInventory.record_time <= datetime.combine(to_date, datetime.max.time()))
    logs = q.order_by(models.MaterialInventory.record_time.desc(), models.MaterialInventory.id.desc()).all()
    result = []
    for log in logs:
        material = log.material
        result.append({
            "id": log.id,
            "material_id": log.material_id,
            "material_code": material.code if material else "",
            "material_name": material.name if material else "",
            "quantity": log.quantity,
            "record_type": log.record_type,
            "reference_no": log.reference_no,
            "operator": log.operator,
            "remark": log.remark,
            "record_time": log.record_time,
            "balance_after": log.balance_after
        })
    return result


@router.post("/inventory", response_model=InventoryOut)
def create_inventory_record(inv_in: InventoryIn, db: Session = Depends(get_db)):
    m = db.query(models.Material).filter(models.Material.id == inv_in.material_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="物料不存在")
    if inv_in.record_type == "出库" and inv_in.quantity > 0:
        inv_in = InventoryIn(**{**inv_in.dict(), "quantity": -abs(inv_in.quantity)})
    if inv_in.record_type == "入库" and inv_in.quantity < 0:
        inv_in = InventoryIn(**{**inv_in.dict(), "quantity": abs(inv_in.quantity)})
    current_stock = get_material_current_stock(db, inv_in.material_id)
    new_balance = current_stock + inv_in.quantity
    if new_balance < 0:
        raise HTTPException(status_code=400, detail=f"库存不足，当前库存{current_stock}，无法出库{abs(inv_in.quantity)}")
    log = models.MaterialInventory(
        material_id=inv_in.material_id,
        quantity=inv_in.quantity,
        record_type=inv_in.record_type,
        reference_no=inv_in.reference_no,
        operator=inv_in.operator,
        remark=inv_in.remark,
        record_time=datetime.now(),
        balance_after=new_balance
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    _recalculate_related_schedules(db, inv_in.material_id)
    material = log.material
    return {
        "id": log.id,
        "material_id": log.material_id,
        "material_code": material.code if material else "",
        "material_name": material.name if material else "",
        "quantity": log.quantity,
        "record_type": log.record_type,
        "reference_no": log.reference_no,
        "operator": log.operator,
        "remark": log.remark,
        "record_time": log.record_time,
        "balance_after": log.balance_after
    }


@router.get("/alerts/low-stock")
def get_low_stock_alerts(db: Session = Depends(get_db)):
    materials = db.query(models.Material).order_by(models.Material.code).all()
    alerts = []
    for m in materials:
        stock = get_material_current_stock(db, m.id)
        if stock <= m.safety_stock:
            alerts.append({
                "material_id": m.id,
                "material_code": m.code,
                "material_name": m.name,
                "category": m.category,
                "unit": m.unit,
                "current_stock": stock,
                "safety_stock": m.safety_stock,
                "shortage": max(0, m.safety_stock - stock)
            })
    return alerts


def _recalculate_related_schedules(db: Session, material_id: int):
    from app.scheduler import auto_recalculate_schedules
    reqs = db.query(models.TaskMaterialRequirement).filter(
        models.TaskMaterialRequirement.material_id == material_id
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
            trigger_source=f"material_change:{material_id}"
        )
    from app.routers.costs import recalculate_ship_costs_and_quotations
    if ship_ids:
        recalculate_ship_costs_and_quotations(db, ship_ids)
