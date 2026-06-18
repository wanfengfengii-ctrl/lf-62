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
