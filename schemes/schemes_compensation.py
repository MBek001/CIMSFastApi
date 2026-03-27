from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from models.user_models import CompensationBonusType, MistakeCategory, MistakeSeverity


class CompensationMistakeCreateRequest(BaseModel):
    employee_id: int
    reviewer_id: Optional[int] = None
    project_id: Optional[int] = None
    category: MistakeCategory
    severity: MistakeSeverity
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=5)
    incident_date: date
    reached_client: bool = True
    unclear_task: bool = False

    @model_validator(mode="after")
    def validate_review_owner(self):
        if (self.reached_client or self.unclear_task) and self.reviewer_id is None:
            raise ValueError("reached_client yoki unclear_task bo'lsa reviewer_id majburiy")
        return self


class CompensationMistakeUpdateRequest(BaseModel):
    employee_id: Optional[int] = None
    reviewer_id: Optional[int] = None
    project_id: Optional[int] = None
    category: Optional[MistakeCategory] = None
    severity: Optional[MistakeSeverity] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, min_length=5)
    incident_date: Optional[date] = None
    reached_client: Optional[bool] = None
    unclear_task: Optional[bool] = None


class DeliveryBonusCreateRequest(BaseModel):
    employee_id: int
    bonus_type: CompensationBonusType
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    award_date: date
    project_id: Optional[int] = None


class DeliveryBonusUpdateRequest(BaseModel):
    employee_id: Optional[int] = None
    bonus_type: Optional[CompensationBonusType] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = None
    award_date: Optional[date] = None
    project_id: Optional[int] = None
