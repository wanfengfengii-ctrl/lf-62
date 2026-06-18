from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Date
from sqlalchemy.orm import relationship
from app.database import Base


class Ship(Base):
    __tablename__ = "ships"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    draft = Column(Float, nullable=False)
    priority = Column(Integer, nullable=False, default=0)

    tasks = relationship("RepairTask", back_populates="ship", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="ship", cascade="all, delete-orphan")


class Dock(Base):
    __tablename__ = "docks"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    min_water_level = Column(Float, nullable=False)

    schedules = relationship("Schedule", back_populates="dock", cascade="all, delete-orphan")


class Tide(Base):
    __tablename__ = "tides"

    id = Column(Integer, primary_key=True, index=True)
    tide_date = Column(Date, nullable=False)
    tide_time = Column(String, nullable=False)
    water_level = Column(Float, nullable=False)

    __mapper_args__ = {
        "confirm_deleted_rows": False
    }


class RepairTask(Base):
    __tablename__ = "repair_tasks"

    id = Column(Integer, primary_key=True, index=True)
    ship_id = Column(Integer, ForeignKey("ships.id"), nullable=False)
    process_type = Column(String, nullable=False)
    duration_hours = Column(Float, nullable=False)

    ship = relationship("Ship", back_populates="tasks")
    material_requirements = relationship("TaskMaterialRequirement", back_populates="task", cascade="all, delete-orphan")
    labor_requirements = relationship("TaskLaborRequirement", back_populates="task", cascade="all, delete-orphan")


PROCESS_TYPES = ["排水", "修补", "上油"]


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    ship_id = Column(Integer, ForeignKey("ships.id"), nullable=False)
    dock_id = Column(Integer, ForeignKey("docks.id"), nullable=False)
    enter_time = Column(DateTime, nullable=False)
    start_drain_time = Column(DateTime, nullable=False)
    start_repair_time = Column(DateTime, nullable=False)
    start_oil_time = Column(DateTime)
    exit_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="draft")
    conflict_reason = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)

    ship = relationship("Ship", back_populates="schedules")
    dock = relationship("Dock", back_populates="schedules")
    material_consumptions = relationship("MaterialConsumption", back_populates="schedule", cascade="all, delete-orphan")


MATERIAL_CATEGORIES = ["木材", "油料", "绳索", "铁件", "其他"]


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False, default="其他")
    unit = Column(String, nullable=False)
    safety_stock = Column(Float, nullable=False, default=0.0)
    description = Column(String, nullable=True)

    inventory_records = relationship("MaterialInventory", back_populates="material", cascade="all, delete-orphan")
    consumptions = relationship("MaterialConsumption", back_populates="material", cascade="all, delete-orphan")
    task_requirements = relationship("TaskMaterialRequirement", back_populates="material", cascade="all, delete-orphan")


class MaterialInventory(Base):
    __tablename__ = "material_inventory"

    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    record_type = Column(String, nullable=False)
    reference_no = Column(String, nullable=True)
    operator = Column(String, nullable=True)
    remark = Column(String, nullable=True)
    record_time = Column(DateTime, nullable=False)
    balance_after = Column(Float, nullable=False)

    material = relationship("Material", back_populates="inventory_records")


class Crew(Base):
    __tablename__ = "crews"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    crew_type = Column(String, nullable=False)
    description = Column(String, nullable=True)

    members = relationship("CrewMember", back_populates="crew", cascade="all, delete-orphan")
    daily_availabilities = relationship("CrewDailyAvailability", back_populates="crew", cascade="all, delete-orphan")
    task_requirements = relationship("TaskLaborRequirement", back_populates="crew", cascade="all, delete-orphan")


CREW_TYPES = ["木工", "油工", "杂工", "起重", "其他"]


class CrewMember(Base):
    __tablename__ = "crew_members"

    id = Column(Integer, primary_key=True, index=True)
    crew_id = Column(Integer, ForeignKey("crews.id"), nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    skill_level = Column(String, nullable=True)
    status = Column(String, nullable=False, default="在职")

    crew = relationship("Crew", back_populates="members")


class CrewDailyAvailability(Base):
    __tablename__ = "crew_daily_availability"

    id = Column(Integer, primary_key=True, index=True)
    crew_id = Column(Integer, ForeignKey("crews.id"), nullable=False)
    work_date = Column(Date, nullable=False)
    available_hours = Column(Float, nullable=False, default=0.0)
    used_hours = Column(Float, nullable=False, default=0.0)
    remark = Column(String, nullable=True)

    crew = relationship("Crew", back_populates="daily_availabilities")


class TaskMaterialRequirement(Base):
    __tablename__ = "task_material_requirements"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("repair_tasks.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Float, nullable=False)

    task = relationship("RepairTask", back_populates="material_requirements")
    material = relationship("Material", back_populates="task_requirements")


class TaskLaborRequirement(Base):
    __tablename__ = "task_labor_requirements"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("repair_tasks.id"), nullable=False)
    crew_type = Column(String, nullable=False)
    crew_id = Column(Integer, ForeignKey("crews.id"), nullable=True)
    required_hours = Column(Float, nullable=False)

    task = relationship("RepairTask", back_populates="labor_requirements")
    crew = relationship("Crew", back_populates="task_requirements")


class MaterialConsumption(Base):
    __tablename__ = "material_consumptions"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    planned_quantity = Column(Float, nullable=False)
    actual_quantity = Column(Float, nullable=True)
    consumption_time = Column(DateTime, nullable=True)
    operator = Column(String, nullable=True)
    remark = Column(String, nullable=True)

    schedule = relationship("Schedule", back_populates="material_consumptions")
    material = relationship("Material", back_populates="consumptions")
