"""Section and Block Pydantic models matching PLAN.md §3.1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KpiItem(BaseModel):
    label: str
    value: str
    delta: str | None = None
    status: Literal["neutral", "success", "warn", "danger"] = "neutral"


class MarkdownBlock(BaseModel):
    type: Literal["markdown"] = "markdown"
    body: str


class KpisBlock(BaseModel):
    type: Literal["kpis"] = "kpis"
    items: list[KpiItem]


class ChartBlock(BaseModel):
    type: Literal["chart"] = "chart"
    id: str
    title: str
    subtitle: str | None = None
    figure: dict[str, Any]
    alt_figures: dict[str, dict[str, Any]] | None = None
    caption: str | None = None


class TableColumn(BaseModel):
    key: str
    label: str
    align: Literal["left", "right", "center"] = "left"


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    id: str
    title: str
    subtitle: str | None = None
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
    caption: str | None = None


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    tone: Literal["info", "warn", "success", "danger"] = "info"
    title: str
    body: str


Block = MarkdownBlock | KpisBlock | ChartBlock | TableBlock | CalloutBlock


class Section(BaseModel):
    """A self-contained analytical section persisted as JSON."""

    id: str
    title: str
    subtitle: str
    blocks: list[Block] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.id
