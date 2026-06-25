from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
