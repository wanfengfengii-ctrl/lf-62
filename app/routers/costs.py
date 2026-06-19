from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, date
from app import models
from app.database import get_db
from app.scheduler import (
    get_material_current_stock,
    get_ship_total_material_requirements,
    get_ship_total_labor_requirements,
    get_process_durations,
)
from collections import defaultdict

router = APIRouter()


class MaterialPriceIn(BaseModel):
    material_id: int
    unit_price: float = Field(..., gt=0)
    effective_date: date
    supplier: Optional[str] = None
    remark: Optional[str] = None


class MaterialPriceOut(BaseModel):
    id: int
    material_id: int
    material_code: str
    material_name: str
    unit_price: float
    effective_date: date
    supplier: Optional[str]
    remark: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class CrewRateIn(BaseModel):
    crew_type: str = Field(..., pattern=r"^(木工|油工|杂工|起重|其他)$")
    hourly_rate: float = Field(..., ge=0)
    effective_date: date
    remark: Optional[str] = None


class CrewRateOut(BaseModel):
    id: int
    crew_type: str
    hourly_rate: float
    effective_date: date
    remark: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class DockRateIn(BaseModel):
    dock_id: int
    daily_rate: float = Field(..., ge=0)
    effective_date: date
    remark: Optional[str] = None


class DockRateOut(BaseModel):
    id: int
    dock_id: int
    dock_code: str
    dock_name: str
    daily_rate: float
    effective_date: date
    remark: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class CostItemOut(BaseModel):
    id: int
    calculation_id: int
    item_type: str
    item_name: str
    item_code: Optional[str]
    category: Optional[str]
    quantity: float
    unit: Optional[str]
    unit_price: float
    total_price: float
    process_type: Optional[str]
    remark: Optional[str]

    class Config:
        from_attributes = True


class CostCalculationOut(BaseModel):
    id: int
    ship_id: int
    ship_code: str
    ship_name: str
    schedule_id: Optional[int]
    calculation_date: datetime
    total_material_cost: float
    total_labor_cost: float
    total_dock_cost: float
    total_other_cost: float
    total_cost: float
    remark: Optional[str]
    created_at: datetime
    updated_at: datetime
    items: List[CostItemOut] = []

    class Config:
        from_attributes = True


class QuotationItemIn(BaseModel):
    item_type: str = Field(..., pattern=r"^(material|labor|dock|other)$")
    item_name: str
    item_code: Optional[str] = None
    category: Optional[str] = None
    quantity: float = Field(..., ge=0)
    unit: Optional[str] = None
    unit_price: float = Field(..., ge=0)
    process_type: Optional[str] = None
    remark: Optional[str] = None


class QuotationItemOut(BaseModel):
    id: int
    quotation_id: int
    item_type: str
    item_name: str
    item_code: Optional[str]
    category: Optional[str]
    quantity: float
    unit: Optional[str]
    unit_price: float
    total_price: float
    process_type: Optional[str]
    remark: Optional[str]

    class Config:
        from_attributes = True


class ApprovalRecordOut(BaseModel):
    id: int
    quotation_id: int
    approver: str
    action: str
    comment: Optional[str]
    approval_time: datetime
    previous_status: Optional[str]
    new_status: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class QuotationIn(BaseModel):
    ship_id: int
    schedule_id: Optional[int] = None
    title: str
    customer_name: Optional[str] = None
    profit_margin: float = Field(0.2, ge=0, le=1)
    tax_rate: float = Field(0.13, ge=0, le=1)
    valid_until: Optional[date] = None
    remark: Optional[str] = None
    items: List[QuotationItemIn] = []


class QuotationUpdateIn(BaseModel):
    title: Optional[str] = None
    customer_name: Optional[str] = None
    profit_margin: Optional[float] = Field(None, ge=0, le=1)
    tax_rate: Optional[float] = Field(None, ge=0, le=1)
    valid_until: Optional[date] = None
    remark: Optional[str] = None
    items: Optional[List[QuotationItemIn]] = None


class QuotationOut(BaseModel):
    id: int
    quotation_no: str
    ship_id: int
    ship_code: str
    ship_name: str
    schedule_id: Optional[int]
    cost_calculation_id: Optional[int]
    version: int
    status: str
    title: str
    customer_name: Optional[str]
    total_cost: float
    profit_margin: float
    profit_amount: float
    tax_rate: float
    tax_amount: float
    total_amount: float
    valid_until: Optional[date]
    current_approver: Optional[str]
    final_confirmation: bool
    parent_id: Optional[int]
    remark: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    confirmed_at: Optional[datetime]
    items: List[QuotationItemOut] = []
    approvals: List[ApprovalRecordOut] = []

    class Config:
        from_attributes = True


class ApprovalIn(BaseModel):
    approver: str
    action: str = Field(..., pattern=r"^(submit|approve|reject|return)$")
    comment: Optional[str] = None


class CostAlertOut(BaseModel):
    id: int
    ship_id: int
    ship_code: str
    ship_name: str
    quotation_id: Optional[int]
    cost_calculation_id: Optional[int]
    alert_type: str
    alert_level: str
    title: str
    description: Optional[str]
    related_item: Optional[str]
    expected_value: Optional[float]
    actual_value: Optional[float]
    difference: Optional[float]
    is_resolved: bool
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


def get_material_latest_price(db: Session, material_id: int, target_date: date = None) -> float:
    if target_date is None:
        target_date = date.today()
    latest = (
        db.query(models.MaterialPrice)
        .filter(
            models.MaterialPrice.material_id == material_id,
            models.MaterialPrice.effective_date <= target_date,
        )
        .order_by(models.MaterialPrice.effective_date.desc(), models.MaterialPrice.id.desc())
        .first()
    )
    return latest.unit_price if latest else 0.0


def get_crew_latest_rate(db: Session, crew_type: str, target_date: date = None) -> float:
    if target_date is None:
        target_date = date.today()
    latest = (
        db.query(models.CrewRate)
        .filter(
            models.CrewRate.crew_type == crew_type,
            models.CrewRate.effective_date <= target_date,
        )
        .order_by(models.CrewRate.effective_date.desc(), models.CrewRate.id.desc())
        .first()
    )
    return latest.hourly_rate if latest else 0.0


def get_dock_latest_rate(db: Session, dock_id: int, target_date: date = None) -> float:
    if target_date is None:
        target_date = date.today()
    latest = (
        db.query(models.DockUsageRate)
        .filter(
            models.DockUsageRate.dock_id == dock_id,
            models.DockUsageRate.effective_date <= target_date,
        )
        .order_by(models.DockUsageRate.effective_date.desc(), models.DockUsageRate.id.desc())
        .first()
    )
    return latest.daily_rate if latest else 0.0


def generate_quotation_no(db: Session) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"Q{today}"
    count = (
        db.query(models.Quotation)
        .filter(models.Quotation.quotation_no.like(f"{prefix}%"))
        .count()
    )
    return f"{prefix}{count + 1:03d}"


@router.get("/material-prices", response_model=List[MaterialPriceOut])
def list_material_prices(
    material_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.MaterialPrice)
    if material_id:
        q = q.filter(models.MaterialPrice.material_id == material_id)
    prices = q.order_by(models.MaterialPrice.effective_date.desc(), models.MaterialPrice.id.desc()).all()
    result = []
    for p in prices:
        m = p.material
        result.append(
            {
                "id": p.id,
                "material_id": p.material_id,
                "material_code": m.code if m else "",
                "material_name": m.name if m else "",
                "unit_price": p.unit_price,
                "effective_date": p.effective_date,
                "supplier": p.supplier,
                "remark": p.remark,
                "created_at": p.created_at,
            }
        )
    return result


@router.post("/material-prices", response_model=MaterialPriceOut)
def create_material_price(price_in: MaterialPriceIn, db: Session = Depends(get_db)):
    material = db.query(models.Material).filter(models.Material.id == price_in.material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="物料不存在")
    p = models.MaterialPrice(**price_in.dict())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {
        "id": p.id,
        "material_id": p.material_id,
        "material_code": material.code,
        "material_name": material.name,
        "unit_price": p.unit_price,
        "effective_date": p.effective_date,
        "supplier": p.supplier,
        "remark": p.remark,
        "created_at": p.created_at,
    }


@router.delete("/material-prices/{price_id}")
def delete_material_price(price_id: int, db: Session = Depends(get_db)):
    p = db.query(models.MaterialPrice).filter(models.MaterialPrice.id == price_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="物料价格记录不存在")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/crew-rates", response_model=List[CrewRateOut])
def list_crew_rates(
    crew_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.CrewRate)
    if crew_type:
        q = q.filter(models.CrewRate.crew_type == crew_type)
    return q.order_by(models.CrewRate.effective_date.desc(), models.CrewRate.id.desc()).all()


@router.post("/crew-rates", response_model=CrewRateOut)
def create_crew_rate(rate_in: CrewRateIn, db: Session = Depends(get_db)):
    r = models.CrewRate(**rate_in.dict())
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.delete("/crew-rates/{rate_id}")
def delete_crew_rate(rate_id: int, db: Session = Depends(get_db)):
    r = db.query(models.CrewRate).filter(models.CrewRate.id == rate_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="班组费率不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/dock-rates", response_model=List[DockRateOut])
def list_dock_rates(
    dock_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.DockUsageRate)
    if dock_id:
        q = q.filter(models.DockUsageRate.dock_id == dock_id)
    rates = q.order_by(models.DockUsageRate.effective_date.desc(), models.DockUsageRate.id.desc()).all()
    result = []
    for r in rates:
        d = r.dock
        result.append(
            {
                "id": r.id,
                "dock_id": r.dock_id,
                "dock_code": d.code if d else "",
                "dock_name": d.name if d else "",
                "daily_rate": r.daily_rate,
                "effective_date": r.effective_date,
                "remark": r.remark,
                "created_at": r.created_at,
            }
        )
    return result


@router.post("/dock-rates", response_model=DockRateOut)
def create_dock_rate(rate_in: DockRateIn, db: Session = Depends(get_db)):
    dock = db.query(models.Dock).filter(models.Dock.id == rate_in.dock_id).first()
    if not dock:
        raise HTTPException(status_code=404, detail="船坞不存在")
    r = models.DockUsageRate(**rate_in.dict())
    db.add(r)
    db.commit()
    db.refresh(r)
    return {
        "id": r.id,
        "dock_id": r.dock_id,
        "dock_code": dock.code,
        "dock_name": dock.name,
        "daily_rate": r.daily_rate,
        "effective_date": r.effective_date,
        "remark": r.remark,
        "created_at": r.created_at,
    }


@router.delete("/dock-rates/{rate_id}")
def delete_dock_rate(rate_id: int, db: Session = Depends(get_db)):
    r = db.query(models.DockUsageRate).filter(models.DockUsageRate.id == rate_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="船坞费率不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


def calculate_ship_cost(db: Session, ship_id: int, schedule_id: int = None, target_date: date = None):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    if target_date is None:
        target_date = date.today()

    schedule = None
    if schedule_id:
        schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()

    cost_items = []
    total_material_cost = 0.0
    total_labor_cost = 0.0
    total_dock_cost = 0.0
    total_other_cost = 0.0

    tasks = db.query(models.RepairTask).filter(models.RepairTask.ship_id == ship_id).all()

    for task in tasks:
        mat_reqs = db.query(models.TaskMaterialRequirement).filter(
            models.TaskMaterialRequirement.task_id == task.id
        ).all()
        for req in mat_reqs:
            material = req.material
            unit_price = get_material_latest_price(db, req.material_id, target_date)
            total_price = req.quantity * unit_price
            total_material_cost += total_price
            cost_items.append(
                {
                    "item_type": "material",
                    "item_name": material.name if material else "未知物料",
                    "item_code": material.code if material else "",
                    "category": material.category if material else "",
                    "quantity": req.quantity,
                    "unit": material.unit if material else "",
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "process_type": task.process_type,
                    "remark": None,
                }
            )

        labor_reqs = db.query(models.TaskLaborRequirement).filter(
            models.TaskLaborRequirement.task_id == task.id
        ).all()
        for req in labor_reqs:
            hourly_rate = get_crew_latest_rate(db, req.crew_type, target_date)
            total_price = req.required_hours * hourly_rate
            total_labor_cost += total_price
            crew = req.crew
            cost_items.append(
                {
                    "item_type": "labor",
                    "item_name": f"{req.crew_type}工时",
                    "item_code": crew.code if crew else "",
                    "category": req.crew_type,
                    "quantity": req.required_hours,
                    "unit": "小时",
                    "unit_price": hourly_rate,
                    "total_price": total_price,
                    "process_type": task.process_type,
                    "remark": None,
                }
            )

    if schedule:
        dock = schedule.dock
        if dock:
            daily_rate = get_dock_latest_rate(db, schedule.dock_id, target_date)
            if schedule.enter_time and schedule.exit_time:
                total_days = max(1, (schedule.exit_time.date() - schedule.enter_time.date()).days + 1)
                total_price = total_days * daily_rate
                total_dock_cost += total_price
                cost_items.append(
                    {
                        "item_type": "dock",
                        "item_name": f"船坞{dock.code}使用费",
                        "item_code": dock.code,
                        "category": "船坞",
                        "quantity": total_days,
                        "unit": "天",
                        "unit_price": daily_rate,
                        "total_price": total_price,
                        "process_type": None,
                        "remark": f"{schedule.enter_time.strftime('%Y-%m-%d')} ~ {schedule.exit_time.strftime('%Y-%m-%d')}",
                    }
                )

    total_cost = total_material_cost + total_labor_cost + total_dock_cost + total_other_cost

    return {
        "ship_id": ship_id,
        "schedule_id": schedule_id,
        "total_material_cost": round(total_material_cost, 2),
        "total_labor_cost": round(total_labor_cost, 2),
        "total_dock_cost": round(total_dock_cost, 2),
        "total_other_cost": round(total_other_cost, 2),
        "total_cost": round(total_cost, 2),
        "items": cost_items,
    }


@router.post("/calculate/{ship_id}", response_model=CostCalculationOut)
def calculate_and_save_cost(
    ship_id: int,
    schedule_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    result = calculate_ship_cost(db, ship_id, schedule_id)

    old_calc = (
        db.query(models.CostCalculation)
        .filter(models.CostCalculation.ship_id == ship_id)
        .order_by(models.CostCalculation.created_at.desc())
        .first()
    )
    if old_calc:
        db.query(models.CostItem).filter(models.CostItem.calculation_id == old_calc.id).delete()
        db.delete(old_calc)

    calc = models.CostCalculation(
        ship_id=ship_id,
        schedule_id=schedule_id,
        total_material_cost=result["total_material_cost"],
        total_labor_cost=result["total_labor_cost"],
        total_dock_cost=result["total_dock_cost"],
        total_other_cost=result["total_other_cost"],
        total_cost=result["total_cost"],
    )
    db.add(calc)
    db.flush()

    for item in result["items"]:
        db.add(models.CostItem(calculation_id=calc.id, **item))

    db.commit()
    db.refresh(calc)

    _check_cost_alerts(db, ship_id, calc)

    return _cost_calc_to_out(calc, db)


def _cost_calc_to_out(calc: models.CostCalculation, db: Session) -> dict:
    ship = calc.ship
    items = []
    for item in calc.items:
        items.append(
            {
                "id": item.id,
                "calculation_id": item.calculation_id,
                "item_type": item.item_type,
                "item_name": item.item_name,
                "item_code": item.item_code,
                "category": item.category,
                "quantity": item.quantity,
                "unit": item.unit,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "process_type": item.process_type,
                "remark": item.remark,
            }
        )
    return {
        "id": calc.id,
        "ship_id": calc.ship_id,
        "ship_code": ship.code if ship else "",
        "ship_name": ship.name if ship else "",
        "schedule_id": calc.schedule_id,
        "calculation_date": calc.calculation_date,
        "total_material_cost": calc.total_material_cost,
        "total_labor_cost": calc.total_labor_cost,
        "total_dock_cost": calc.total_dock_cost,
        "total_other_cost": calc.total_other_cost,
        "total_cost": calc.total_cost,
        "remark": calc.remark,
        "created_at": calc.created_at,
        "updated_at": calc.updated_at,
        "items": items,
    }


@router.get("/calculations/{ship_id}", response_model=List[CostCalculationOut])
def list_ship_cost_calculations(ship_id: int, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")
    calcs = (
        db.query(models.CostCalculation)
        .filter(models.CostCalculation.ship_id == ship_id)
        .order_by(models.CostCalculation.created_at.desc())
        .all()
    )
    return [_cost_calc_to_out(c, db) for c in calcs]


@router.get("/calculation/{calc_id}", response_model=CostCalculationOut)
def get_cost_calculation(calc_id: int, db: Session = Depends(get_db)):
    calc = db.query(models.CostCalculation).filter(models.CostCalculation.id == calc_id).first()
    if not calc:
        raise HTTPException(status_code=404, detail="成本核算记录不存在")
    return _cost_calc_to_out(calc, db)


def _quotation_to_out(q: models.Quotation, db: Session) -> dict:
    ship = q.ship
    items = []
    for item in q.items:
        items.append(
            {
                "id": item.id,
                "quotation_id": item.quotation_id,
                "item_type": item.item_type,
                "item_name": item.item_name,
                "item_code": item.item_code,
                "category": item.category,
                "quantity": item.quantity,
                "unit": item.unit,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "process_type": item.process_type,
                "remark": item.remark,
            }
        )
    approvals = []
    for a in q.approvals:
        approvals.append(
            {
                "id": a.id,
                "quotation_id": a.quotation_id,
                "approver": a.approver,
                "action": a.action,
                "comment": a.comment,
                "approval_time": a.approval_time,
                "previous_status": a.previous_status,
                "new_status": a.new_status,
                "created_at": a.created_at,
            }
        )
    return {
        "id": q.id,
        "quotation_no": q.quotation_no,
        "ship_id": q.ship_id,
        "ship_code": ship.code if ship else "",
        "ship_name": ship.name if ship else "",
        "schedule_id": q.schedule_id,
        "cost_calculation_id": q.cost_calculation_id,
        "version": q.version,
        "status": q.status,
        "title": q.title,
        "customer_name": q.customer_name,
        "total_cost": q.total_cost,
        "profit_margin": q.profit_margin,
        "profit_amount": q.profit_amount,
        "tax_rate": q.tax_rate,
        "tax_amount": q.tax_amount,
        "total_amount": q.total_amount,
        "valid_until": q.valid_until,
        "current_approver": q.current_approver,
        "final_confirmation": q.final_confirmation,
        "parent_id": q.parent_id,
        "remark": q.remark,
        "created_by": q.created_by,
        "created_at": q.created_at,
        "updated_at": q.updated_at,
        "confirmed_at": q.confirmed_at,
        "items": items,
        "approvals": approvals,
    }


@router.get("/quotations", response_model=List[QuotationOut])
def list_quotations(
    ship_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.Quotation)
    if ship_id:
        q = q.filter(models.Quotation.ship_id == ship_id)
    if status:
        q = q.filter(models.Quotation.status == status)
    quotations = q.order_by(models.Quotation.created_at.desc()).all()
    return [_quotation_to_out(qt, db) for qt in quotations]


@router.get("/quotations/{quotation_id}", response_model=QuotationOut)
def get_quotation(quotation_id: int, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")
    return _quotation_to_out(q, db)


def _compute_quotation_amounts(
    total_cost: float, profit_margin: float, tax_rate: float
) -> dict:
    profit_amount = round(total_cost * profit_margin, 2)
    subtotal = total_cost + profit_amount
    tax_amount = round(subtotal * tax_rate, 2)
    total_amount = round(subtotal + tax_amount, 2)
    return {
        "total_cost": round(total_cost, 2),
        "profit_amount": profit_amount,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
    }


@router.post("/quotations", response_model=QuotationOut)
def create_quotation(quotation_in: QuotationIn, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == quotation_in.ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    quotation_no = generate_quotation_no(db)

    if quotation_in.items:
        total_cost = round(sum(it.quantity * it.unit_price for it in quotation_in.items), 2)
    else:
        cost_result = calculate_ship_cost(db, quotation_in.ship_id, quotation_in.schedule_id)
        total_cost = cost_result["total_cost"]

    amounts = _compute_quotation_amounts(
        total_cost, quotation_in.profit_margin, quotation_in.tax_rate
    )

    q = models.Quotation(
        quotation_no=quotation_no,
        ship_id=quotation_in.ship_id,
        schedule_id=quotation_in.schedule_id,
        version=1,
        status="draft",
        title=quotation_in.title,
        customer_name=quotation_in.customer_name,
        profit_margin=quotation_in.profit_margin,
        tax_rate=quotation_in.tax_rate,
        valid_until=quotation_in.valid_until,
        remark=quotation_in.remark,
        **amounts,
    )
    db.add(q)
    db.flush()

    if quotation_in.items:
        for it in quotation_in.items:
            db.add(
                models.QuotationItem(
                    quotation_id=q.id,
                    item_type=it.item_type,
                    item_name=it.item_name,
                    item_code=it.item_code,
                    category=it.category,
                    quantity=it.quantity,
                    unit=it.unit,
                    unit_price=it.unit_price,
                    total_price=round(it.quantity * it.unit_price, 2),
                    process_type=it.process_type,
                    remark=it.remark,
                )
            )
    else:
        cost_result = calculate_ship_cost(db, quotation_in.ship_id, quotation_in.schedule_id)
        for it in cost_result["items"]:
            db.add(models.QuotationItem(quotation_id=q.id, **it))

    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.post("/quotations/from-calculation/{calc_id}", response_model=QuotationOut)
def create_quotation_from_calculation(
    calc_id: int,
    title: str,
    customer_name: Optional[str] = None,
    profit_margin: float = 0.2,
    tax_rate: float = 0.13,
    valid_until: Optional[date] = None,
    remark: Optional[str] = None,
    db: Session = Depends(get_db),
):
    calc = db.query(models.CostCalculation).filter(models.CostCalculation.id == calc_id).first()
    if not calc:
        raise HTTPException(status_code=404, detail="成本核算记录不存在")

    quotation_no = generate_quotation_no(db)
    amounts = _compute_quotation_amounts(calc.total_cost, profit_margin, tax_rate)

    q = models.Quotation(
        quotation_no=quotation_no,
        ship_id=calc.ship_id,
        schedule_id=calc.schedule_id,
        cost_calculation_id=calc.id,
        version=1,
        status="draft",
        title=title,
        customer_name=customer_name,
        profit_margin=profit_margin,
        tax_rate=tax_rate,
        valid_until=valid_until,
        remark=remark,
        **amounts,
    )
    db.add(q)
    db.flush()

    for item in calc.items:
        db.add(
            models.QuotationItem(
                quotation_id=q.id,
                item_type=item.item_type,
                item_name=item.item_name,
                item_code=item.item_code,
                category=item.category,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                total_price=item.total_price,
                process_type=item.process_type,
                remark=item.remark,
            )
        )

    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.put("/quotations/{quotation_id}", response_model=QuotationOut)
def update_quotation(
    quotation_id: int, update_in: QuotationUpdateIn, db: Session = Depends(get_db)
):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")
    if q.status not in ["draft", "rejected"]:
        raise HTTPException(status_code=400, detail="只有草稿或已驳回状态的报价单可以修改")

    if update_in.title is not None:
        q.title = update_in.title
    if update_in.customer_name is not None:
        q.customer_name = update_in.customer_name
    if update_in.profit_margin is not None:
        q.profit_margin = update_in.profit_margin
    if update_in.tax_rate is not None:
        q.tax_rate = update_in.tax_rate
    if update_in.valid_until is not None:
        q.valid_until = update_in.valid_until
    if update_in.remark is not None:
        q.remark = update_in.remark

    if update_in.items is not None:
        db.query(models.QuotationItem).filter(models.QuotationItem.quotation_id == q.id).delete()
        for it in update_in.items:
            db.add(
                models.QuotationItem(
                    quotation_id=q.id,
                    item_type=it.item_type,
                    item_name=it.item_name,
                    item_code=it.item_code,
                    category=it.category,
                    quantity=it.quantity,
                    unit=it.unit,
                    unit_price=it.unit_price,
                    total_price=round(it.quantity * it.unit_price, 2),
                    process_type=it.process_type,
                    remark=it.remark,
                )
            )
        total_cost = round(sum(it.quantity * it.unit_price for it in update_in.items), 2)
    else:
        total_cost = sum(it.total_price for it in q.items)

    amounts = _compute_quotation_amounts(total_cost, q.profit_margin, q.tax_rate)
    q.total_cost = amounts["total_cost"]
    q.profit_amount = amounts["profit_amount"]
    q.tax_amount = amounts["tax_amount"]
    q.total_amount = amounts["total_amount"]

    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.post("/quotations/{quotation_id}/new-version", response_model=QuotationOut)
def create_new_version(quotation_id: int, db: Session = Depends(get_db)):
    old_q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not old_q:
        raise HTTPException(status_code=404, detail="报价单不存在")

    new_version = old_q.version + 1
    quotation_no = generate_quotation_no(db)

    q = models.Quotation(
        quotation_no=quotation_no,
        ship_id=old_q.ship_id,
        schedule_id=old_q.schedule_id,
        cost_calculation_id=old_q.cost_calculation_id,
        version=new_version,
        status="draft",
        title=old_q.title,
        customer_name=old_q.customer_name,
        total_cost=old_q.total_cost,
        profit_margin=old_q.profit_margin,
        profit_amount=old_q.profit_amount,
        tax_rate=old_q.tax_rate,
        tax_amount=old_q.tax_amount,
        total_amount=old_q.total_amount,
        valid_until=old_q.valid_until,
        parent_id=old_q.id,
        remark=old_q.remark,
    )
    db.add(q)
    db.flush()

    for item in old_q.items:
        db.add(
            models.QuotationItem(
                quotation_id=q.id,
                item_type=item.item_type,
                item_name=item.item_name,
                item_code=item.item_code,
                category=item.category,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                total_price=item.total_price,
                process_type=item.process_type,
                remark=item.remark,
            )
        )

    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.post("/quotations/{quotation_id}/approval", response_model=QuotationOut)
def process_approval(quotation_id: int, approval_in: ApprovalIn, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")

    previous_status = q.status
    action = approval_in.action

    if action == "submit":
        if q.status not in ["draft", "rejected"]:
            raise HTTPException(status_code=400, detail="当前状态不能提交审批")
        q.status = "pending_approval"
        q.current_approver = approval_in.approver
    elif action == "approve":
        if q.status != "pending_approval":
            raise HTTPException(status_code=400, detail="只有待审批状态的报价单可以审批通过")
        q.status = "approved"
        q.current_approver = None
    elif action == "reject":
        if q.status != "pending_approval":
            raise HTTPException(status_code=400, detail="只有待审批状态的报价单可以驳回")
        q.status = "rejected"
        q.current_approver = None
    elif action == "return":
        if q.status != "pending_approval":
            raise HTTPException(status_code=400, detail="只有待审批状态的报价单可以退回")
        q.status = "draft"
        q.current_approver = None

    approval = models.ApprovalRecord(
        quotation_id=q.id,
        approver=approval_in.approver,
        action=action,
        comment=approval_in.comment,
        previous_status=previous_status,
        new_status=q.status,
    )
    db.add(approval)
    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.post("/quotations/{quotation_id}/confirm", response_model=QuotationOut)
def confirm_quotation(quotation_id: int, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")
    if q.status != "approved":
        raise HTTPException(status_code=400, detail="只有已审批通过的报价单可以最终确认")
    q.status = "confirmed"
    q.final_confirmation = True
    q.confirmed_at = datetime.now()
    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.post("/quotations/{quotation_id}/cancel", response_model=QuotationOut)
def cancel_quotation(quotation_id: int, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")
    if q.status == "confirmed":
        raise HTTPException(status_code=400, detail="已确认的报价单不能取消")
    q.status = "cancelled"
    q.current_approver = None
    db.commit()
    db.refresh(q)
    return _quotation_to_out(q, db)


@router.delete("/quotations/{quotation_id}")
def delete_quotation(quotation_id: int, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")
    if q.status in ["approved", "confirmed"]:
        raise HTTPException(status_code=400, detail="已审批或已确认的报价单不能删除")
    db.delete(q)
    db.commit()
    return {"ok": True}


@router.get("/quotations/{quotation_id}/versions", response_model=List[QuotationOut])
def get_quotation_versions(quotation_id: int, db: Session = Depends(get_db)):
    q = db.query(models.Quotation).filter(models.Quotation.id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="报价单不存在")

    root_id = q.id
    while q.parent_id:
        root_id = q.parent_id
        q = db.query(models.Quotation).filter(models.Quotation.id == q.parent_id).first()

    all_versions = []
    stack = [root_id]
    while stack:
        vid = stack.pop()
        vq = db.query(models.Quotation).filter(models.Quotation.id == vid).first()
        if vq:
            all_versions.append(_quotation_to_out(vq, db))
            children = (
                db.query(models.Quotation)
                .filter(models.Quotation.parent_id == vid)
                .all()
            )
            for c in children:
                stack.append(c.id)

    all_versions.sort(key=lambda x: (x["version"], x["created_at"]))
    return all_versions


@router.get("/compare/{ship_id}")
def compare_ship_quotations(ship_id: int, db: Session = Depends(get_db)):
    ship = db.query(models.Ship).filter(models.Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="船只不存在")

    quotations = (
        db.query(models.Quotation)
        .filter(models.Quotation.ship_id == ship_id)
        .order_by(models.Quotation.created_at.desc())
        .all()
    )

    result = {
        "ship_id": ship_id,
        "ship_code": ship.code,
        "ship_name": ship.name,
        "quotations": [],
    }

    for q in quotations:
        q_out = _quotation_to_out(q, db)
        result["quotations"].append(
            {
                "id": q_out["id"],
                "quotation_no": q_out["quotation_no"],
                "version": q_out["version"],
                "status": q_out["status"],
                "title": q_out["title"],
                "total_cost": q_out["total_cost"],
                "profit_amount": q_out["profit_amount"],
                "tax_amount": q_out["tax_amount"],
                "total_amount": q_out["total_amount"],
                "created_at": q_out["created_at"],
            }
        )

    return result


def _check_cost_alerts(db: Session, ship_id: int, calc: models.CostCalculation):
    alerts = []

    previous = (
        db.query(models.CostCalculation)
        .filter(
            models.CostCalculation.ship_id == ship_id,
            models.CostCalculation.id != calc.id,
        )
        .order_by(models.CostCalculation.created_at.desc())
        .first()
    )

    if previous and previous.total_cost > 0:
        increase_pct = (calc.total_cost - previous.total_cost) / previous.total_cost * 100
        if increase_pct >= 20:
            level = "critical" if increase_pct >= 50 else "high"
            alerts.append(
                {
                    "ship_id": ship_id,
                    "cost_calculation_id": calc.id,
                    "alert_type": "cost_increase",
                    "alert_level": level,
                    "title": f"成本大幅上涨{round(increase_pct, 1)}%",
                    "description": f"上次核算成本{previous.total_cost}元，本次{calc.total_cost}元，涨幅{round(increase_pct, 1)}%",
                    "related_item": "总成本",
                    "expected_value": previous.total_cost,
                    "actual_value": calc.total_cost,
                    "difference": calc.total_cost - previous.total_cost,
                }
            )

    material_req = get_ship_total_material_requirements(db, ship_id)
    for mat_id, needed in material_req.items():
        current = get_material_current_stock(db, mat_id)
        shortage = needed - current
        if shortage > 0:
            material = db.query(models.Material).filter(models.Material.id == mat_id).first()
            alerts.append(
                {
                    "ship_id": ship_id,
                    "cost_calculation_id": calc.id,
                    "alert_type": "material_shortage",
                    "alert_level": "medium" if shortage / needed <= 0.3 else "high",
                    "title": f"物料短缺：{material.name if material else '未知'}",
                    "description": f"需要{needed}{material.unit if material else ''}，库存仅{current}{material.unit if material else ''}，短缺{shortage}{material.unit if material else ''}",
                    "related_item": material.code if material else "",
                    "expected_value": needed,
                    "actual_value": current,
                    "difference": shortage,
                }
            )

    for item in calc.items:
        if item.item_type == "material" and item.unit_price == 0:
            alerts.append(
                {
                    "ship_id": ship_id,
                    "cost_calculation_id": calc.id,
                    "alert_type": "abnormal_item",
                    "alert_level": "medium",
                    "title": f"物料未配置价格：{item.item_name}",
                    "description": f"物料{item.item_name}({item.item_code})未设置最新采购价格，成本可能不准确",
                    "related_item": item.item_code,
                    "expected_value": 0,
                    "actual_value": 0,
                    "difference": 0,
                }
            )
        if item.item_type == "labor" and item.unit_price == 0:
            alerts.append(
                {
                    "ship_id": ship_id,
                    "cost_calculation_id": calc.id,
                    "alert_type": "abnormal_item",
                    "alert_level": "medium",
                    "title": f"班组未配置费率：{item.item_name}",
                    "description": f"{item.item_name}未设置工时费率，成本可能不准确",
                    "related_item": item.category,
                    "expected_value": 0,
                    "actual_value": 0,
                    "difference": 0,
                }
            )

    for alert_data in alerts:
        alert = models.CostAlert(**alert_data)
        db.add(alert)


@router.get("/alerts", response_model=List[CostAlertOut])
def list_cost_alerts(
    ship_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    unresolved_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(models.CostAlert)
    if ship_id:
        q = q.filter(models.CostAlert.ship_id == ship_id)
    if alert_type:
        q = q.filter(models.CostAlert.alert_type == alert_type)
    if unresolved_only:
        q = q.filter(models.CostAlert.is_resolved == False)
    alerts = q.order_by(models.CostAlert.created_at.desc()).all()
    result = []
    for a in alerts:
        ship = a.ship
        result.append(
            {
                "id": a.id,
                "ship_id": a.ship_id,
                "ship_code": ship.code if ship else "",
                "ship_name": ship.name if ship else "",
                "quotation_id": a.quotation_id,
                "cost_calculation_id": a.cost_calculation_id,
                "alert_type": a.alert_type,
                "alert_level": a.alert_level,
                "title": a.title,
                "description": a.description,
                "related_item": a.related_item,
                "expected_value": a.expected_value,
                "actual_value": a.actual_value,
                "difference": a.difference,
                "is_resolved": a.is_resolved,
                "resolved_by": a.resolved_by,
                "resolved_at": a.resolved_at,
                "created_at": a.created_at,
            }
        )
    return result


@router.post("/alerts/{alert_id}/resolve")
def resolve_cost_alert(
    alert_id: int,
    resolved_by: str = "system",
    db: Session = Depends(get_db),
):
    a = db.query(models.CostAlert).filter(models.CostAlert.id == alert_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="预警不存在")
    a.is_resolved = True
    a.resolved_by = resolved_by
    a.resolved_at = datetime.now()
    db.commit()
    return {"ok": True}


def recalculate_ship_costs_and_quotations(db: Session, ship_ids: List[int]):
    if not ship_ids:
        return

    for ship_id in ship_ids:
        try:
            latest = (
                db.query(models.CostCalculation)
                .filter(models.CostCalculation.ship_id == ship_id)
                .order_by(models.CostCalculation.created_at.desc())
                .first()
            )
            schedule_id = latest.schedule_id if latest else None

            result = calculate_ship_cost(db, ship_id, schedule_id)

            if latest:
                db.query(models.CostItem).filter(models.CostItem.calculation_id == latest.id).delete()
                latest.total_material_cost = result["total_material_cost"]
                latest.total_labor_cost = result["total_labor_cost"]
                latest.total_dock_cost = result["total_dock_cost"]
                latest.total_other_cost = result["total_other_cost"]
                latest.total_cost = result["total_cost"]
                latest.updated_at = datetime.now()
                for item in result["items"]:
                    db.add(models.CostItem(calculation_id=latest.id, **item))

                _check_cost_alerts(db, ship_id, latest)

            draft_q = (
                db.query(models.Quotation)
                .filter(
                    models.Quotation.ship_id == ship_id,
                    models.Quotation.status.in_(["draft", "rejected"]),
                )
                .all()
            )
            for q in draft_q:
                items = []
                total_cost = 0.0
                for it in result["items"]:
                    item_total = it["quantity"] * it["unit_price"]
                    total_cost += item_total
                    items.append({**it, "total_price": round(item_total, 2)})

                db.query(models.QuotationItem).filter(models.QuotationItem.quotation_id == q.id).delete()
                for it in items:
                    db.add(models.QuotationItem(quotation_id=q.id, **it))

                amounts = _compute_quotation_amounts(total_cost, q.profit_margin, q.tax_rate)
                q.total_cost = amounts["total_cost"]
                q.profit_amount = amounts["profit_amount"]
                q.tax_amount = amounts["tax_amount"]
                q.total_amount = amounts["total_amount"]
                q.updated_at = datetime.now()
        except Exception:
            continue

    db.commit()
