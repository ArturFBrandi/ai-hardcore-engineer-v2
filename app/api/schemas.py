"""HTTP request/response models (Pydantic v2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 2000
# C0 controls (except tab/newline/CR) and DEL: PostgreSQL text rejects NUL, and none of them carry meaning in chat.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: str) -> str:
    return _CONTROL.sub("", value)


class ErrorResponse(BaseModel):
    """Uniform error envelope for every non-2xx response."""

    error: str
    detail: str
    request_id: str | None = None


class ChatRequest(BaseModel):
    """The exact challenge payload (``message`` + ``user_id``) plus an optional conversation id."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    user_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^conv_[A-Za-z0-9]{8,40}$")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = clean_text(value)
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class SourceOut(BaseModel):
    title: str
    url: str
    category: str
    retrieved_at: str | None = None


class ChatResponse(BaseModel):
    request_id: str
    trace_id: str
    conversation_id: str
    turn: int
    status: Literal["answered", "escalated", "awaiting_human", "human_replied", "blocked"]
    answer: str
    intent: str
    agents: list[str]
    route_source: str
    confidence: float
    escalated: bool
    escalation_reasons: list[str]
    case_id: str | None
    sources: list[SourceOut]
    language: str
    plan_steps: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)


class TurnOut(BaseModel):
    turn: int
    role: str
    content: str
    intent: str | None = None
    agents: list[str] = Field(default_factory=list)
    case_id: str | None = None
    sources: list[SourceOut] = Field(default_factory=list)
    created_at: datetime


class ConversationOut(BaseModel):
    conversation_id: str
    status: str
    language: str | None
    created_at: datetime
    updated_at: datetime
    open_case_id: str | None = None
    turns: list[TurnOut] = Field(default_factory=list)


class ConversationListOut(BaseModel):
    items: list[ConversationOut]
    next_offset: int | None


class OperatorReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    send_as_is: bool = False

    @field_validator("message")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = clean_text(value)
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class OperatorDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "edit"]
    edited_arguments: dict[str, Any] | None = None
    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value: str) -> str:
        return clean_text(value)


class OperatorResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z0-9_]+$")
    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value: str) -> str:
        return clean_text(value)


class DevTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sub: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    scope: str = Field(default="chat conversations:read conversations:delete", max_length=200)
