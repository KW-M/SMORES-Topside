"""Shared pydantic models used by the hardware, db, and api layers."""

from models.readings import ScanResult, SensorReading

__all__ = ["ScanResult", "SensorReading"]
