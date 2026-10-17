"""Strict request schemas; financial decimals stay strings on the wire."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class StrictBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class LoginBody(StrictBody):
    username: Annotated[str, Field(min_length=1, max_length=100)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class PasswordChangeBody(StrictBody):
    current_password: Annotated[str, Field(min_length=1, max_length=1024)]
    new_password: Annotated[str, Field(min_length=12, max_length=1024)]


class StrategyTargetBody(StrictBody):
    version: Annotated[str, Field(min_length=1, max_length=100)]
    weights: dict[str, str]


class StrategyPreviewBody(StrictBody):
    weights: dict[str, str]


class CredentialBody(StrictBody):
    values: dict[Annotated[str, Field(min_length=1, max_length=60)], Annotated[str, Field(min_length=1, max_length=16_384)]]


class ThemeBody(StrictBody):
    theme: Annotated[str, Field(pattern=r"^(light|dark|system)$")]


class PushSubscriptionBody(StrictBody):
    endpoint: Annotated[str, Field(min_length=12, max_length=4096)]
    keys: dict[str, Annotated[str, Field(min_length=1, max_length=4096)]]
    device_label: Annotated[str, Field(min_length=1, max_length=100)]


class PushDeleteBody(StrictBody):
    endpoint: Annotated[str, Field(min_length=12, max_length=4096)]
