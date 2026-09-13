from pydantic import BaseModel


class SystemHealth(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class DatabaseHealth(BaseModel):
    status: str
    database: str | None = None
    dialect: str = "mysql"


class SystemReadiness(BaseModel):
    status: str
    database: DatabaseHealth
