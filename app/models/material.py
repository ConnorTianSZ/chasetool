"""
Pydantic 模型：物料行
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator
import json


class MaterialBase(BaseModel):
    po_number: str
    item_no: str
    part_no: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    supplier: Optional[str] = None
    wbs_element: Optional[str] = None
    project_no: Optional[str] = None
    station_no: Optional[str] = None
    purchasing_group: Optional[str] = None
    order_date: Optional[date] = None

    original_eta: Optional[date] = None
    current_eta: Optional[date] = None
    current_eta_source: Optional[str] = None
    supplier_eta: Optional[date] = None
    supplier_feedback_time: Optional[datetime] = None
    supplier_remarks: Optional[str] = None
    supplier_remarks_source: Optional[str] = None

    plant: Optional[str] = None
    supplier_code: Optional[str] = None
    statical_delivery_date: Optional[date] = None
    manufacturer: Optional[str] = None
    manufacturer_part_no: Optional[str] = None
    open_quantity_gr: Optional[float] = None
    net_order_price: Optional[float] = None
    currency: Optional[str] = None
    net_order_value: Optional[float] = None
    position_text1: Optional[str] = None
    position_text2: Optional[str] = None

    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    status: str = "open"

    is_focus: bool = False
    focus_reason: Optional[str] = None

    chase_count: int = 0
    last_chase_time: Optional[datetime] = None
    last_chased_at: Optional[datetime] = None
    last_feedback_chase_count: Optional[int] = None
    escalation_flag: bool = False

    extra_json: Optional[dict[str, Any]] = None


class MaterialCreate(MaterialBase):
    pass


class MaterialRead(MaterialBase):
    id: int
    created_at: datetime
    updated_at: datetime

    @field_validator("extra_json", mode="before")
    @classmethod
    def parse_extra_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v

    model_config = {"from_attributes": True}


class MaterialUpdate(BaseModel):
    """部分更新"""
    part_no: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    supplier: Optional[str] = None
    wbs_element: Optional[str] = None
    project_no: Optional[str] = None
    station_no: Optional[str] = None
    purchasing_group: Optional[str] = None
    order_date: Optional[date] = None
    plant: Optional[str] = None
    supplier_code: Optional[str] = None
    statical_delivery_date: Optional[date] = None
    manufacturer: Optional[str] = None
    manufacturer_part_no: Optional[str] = None
    open_quantity_gr: Optional[float] = None
    net_order_price: Optional[float] = None
    currency: Optional[str] = None
    net_order_value: Optional[float] = None
    position_text1: Optional[str] = None
    position_text2: Optional[str] = None
    original_eta: Optional[date] = None
    current_eta: Optional[date] = None
    supplier_eta: Optional[date] = None
    supplier_remarks: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    status: Optional[str] = None
    is_focus: Optional[bool] = None
    focus_reason: Optional[str] = None
    escalation_flag: Optional[bool] = None
    # 采购员加急跟进后手工记录的最新交期（不被 Excel 导入覆盖）
    urgent_feedback_eta: Optional[date] = None
    urgent_feedback_note: Optional[str] = None


class FieldUpdateRead(BaseModel):
    id: int
    material_id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    source: str
    source_ref: Optional[str] = None
    operator: Optional[str] = None
    confirmed: bool
    timestamp: datetime

    model_config = {"from_attributes": True}


class ImportRecord(BaseModel):
    id: int
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    rows_added: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    imported_at: datetime

    model_config = {"from_attributes": True}


class InboundEmailRead(BaseModel):
    id: int
    outlook_entry_id: Optional[str] = None
    from_address: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    received_at: Optional[datetime] = None
    parsed_marker: Optional[str] = None
    matched_material_id: Optional[int] = None
    llm_extracted_json: Optional[dict] = None
    status: str = "new"
    confirmed_at: Optional[datetime] = None
    operator_decision: Optional[str] = None

    @field_validator("llm_extracted_json", mode="before")
    @classmethod
    def parse_llm_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v

    model_config = {"from_attributes": True}
