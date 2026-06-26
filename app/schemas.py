from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class GeneratedQuestion(BaseModel):
    stem: str = Field(min_length=12, max_length=600)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3)
    difficulty: Literal["recall", "understanding", "application"]
    seconds: int = Field(ge=30, le=30)
    source_quote: str = Field(min_length=8, max_length=500)
    source_location: str = Field(min_length=2, max_length=255)
    explanation: str = Field(min_length=8, max_length=700)

    @field_validator("options")
    @classmethod
    def options_must_be_unique(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if len({item.casefold() for item in cleaned}) != 4:
            raise ValueError("Options must be unique")
        if any(not item for item in cleaned):
            raise ValueError("Options cannot be blank")
        return cleaned


class GeneratedQuestionBank(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=20, max_length=20)


class StartAssessmentRequest(BaseModel):
    document_id: str


class AnswerRequest(BaseModel):
    selected_index: int = Field(ge=0, le=3)


class FocusEventRequest(BaseModel):
    event: Literal["blur", "hidden"] = "hidden"


class LecturerRegisterRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=180)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    institution_name: str = Field(min_length=3, max_length=240)
    department: str = Field(min_length=2, max_length=180)
    staff_id: str = Field(min_length=2, max_length=100)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value):
            raise ValueError("Password must include uppercase and lowercase letters.")
        if not re.search(r"\d", value):
            raise ValueError("Password must include a number.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"\d", value):
            raise ValueError("New password must include uppercase, lowercase, and a number.")
        return value


class CourseCreateRequest(BaseModel):
    course_code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=3, max_length=240)
    academic_year: str = Field(min_length=4, max_length=40)
    semester: str = Field(min_length=2, max_length=80)


class CourseCollaboratorRequest(BaseModel):
    email: EmailStr
    access_level: Literal["co_lecturer", "viewer"] = "co_lecturer"


class UserApprovalRequest(BaseModel):
    role: Literal["lecturer", "institution_admin"] = "lecturer"


class UserSuspensionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
