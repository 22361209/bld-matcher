from __future__ import annotations

from pydantic import Field

from app.platform.api_schemas import StrictApiModel


class CustomerListQuery(StrictApiModel):
    q: str = Field(default="", max_length=300, description="客户名称的模糊匹配片段（如客户简称）。")
    limit: int = Field(default=20, ge=1, le=50)


class CustomerCreateRequest(StrictApiModel):
    name: str = Field(min_length=1, max_length=200, description="客户名称；必须与已登记客户不重复。")


class CustomerResponse(StrictApiModel):
    name: str


class CustomerDetailResponse(StrictApiModel):
    id: int
    name: str
    code: str
    status: str
    created_at: str
    updated_at: str


class CustomerData(StrictApiModel):
    customer: CustomerDetailResponse


class CustomerListData(StrictApiModel):
    customers: list[CustomerResponse]
    total: int


class CustomerListEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: CustomerListData
    warnings: list[str] = Field(default_factory=list)


class CustomerEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: CustomerData
    warnings: list[str] = Field(default_factory=list)
