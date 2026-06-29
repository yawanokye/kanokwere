from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d).+$")


def validate_password(value: str, label: str = "Password") -> str:
    if not PASSWORD_PATTERN.match(value):
        raise ValueError(f"{label} must include uppercase, lowercase, and a number.")
    return value


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

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return validate_password(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ActivateAccountRequest(BaseModel):
    email: EmailStr
    setup_code: str = Field(min_length=8, max_length=40)
    new_password: str = Field(min_length=10, max_length=128)
    recovery_pin: str = Field(pattern=r"^\d{6}$")

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class SelfServicePasswordResetRequest(BaseModel):
    email: EmailStr
    recovery_pin: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class CourseCreateRequest(BaseModel):
    course_code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=3, max_length=240)
    academic_year: str = Field(min_length=4, max_length=40)
    semester: str = Field(min_length=2, max_length=80)
    assessment_question_count: int = Field(default=20, ge=5, le=20)


class CourseSettingsRequest(BaseModel):
    assessment_question_count: int = Field(ge=5, le=20)


class MonitoringEventRequest(BaseModel):
    event_type: Literal[
        "no_face",
        "multiple_faces",
        "looking_away",
        "low_light",
        "camera_interrupted",
        "tab_hidden",
    ]
    duration_ms: int = Field(default=0, ge=0, le=300000)
    question_position: int | None = Field(default=None, ge=1, le=50)
    severity: Literal["warning", "critical"] = "warning"
    corrected: bool = False
    message: str | None = Field(default=None, max_length=300)


class CourseCollaboratorRequest(BaseModel):
    email: EmailStr
    access_level: Literal["co_lecturer", "viewer"] = "co_lecturer"


class UserApprovalRequest(BaseModel):
    role: Literal["lecturer", "institution_admin"] = "lecturer"


class UserSuspensionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PasswordResetRequestCreate(BaseModel):
    email: EmailStr


class AdminUserCreateRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=180)
    email: EmailStr
    institution_name: str = Field(min_length=3, max_length=240)
    department: str = Field(min_length=2, max_length=180)
    role: Literal["lecturer", "institution_admin"] = "lecturer"


class AdminPasswordResetRequest(BaseModel):
    pass


class AdminUserStatusRequest(BaseModel):
    status: Literal["active", "suspended"]
