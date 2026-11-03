"""Section and Block Pydantic models matching PLAN.md §3.1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_STATUS_TO_TONE: dict[str, Literal["neutral", "good", "warn", "bad", "info"]] = {
    "neutral": "neutral",
    "success": "good",
    "good": "good",
    "warn": "warn",
    "danger": "bad",
    "bad": "bad",
    "info": "info",
}


class KpiItem(BaseModel):
    label: str
    value: str
    delta: str | None = None
    status: Literal["neutral", "success", "warn", "danger", "good", "bad", "info"] = "neutral"
    tone: Literal["neutral", "good", "warn", "bad", "info"] | None = None
    note: str | None = None
    help: str | None = None

    @model_validator(mode="after")
    def _sync_tone_and_help(self) -> KpiItem:
        if self.tone is None:
            self.tone = _STATUS_TO_TONE.get(self.status, "neutral")
        if self.help is None and self.note is not None:
            self.help = self.note
        return self


class MarkdownBlock(BaseModel):
    type: Literal["markdown"] = "markdown"
    title: str | None = None
    body: str


class KpisBlock(BaseModel):
    type: Literal["kpis"] = "kpis"
    # Optional, for pages that place a section's KPI rows separately (the forecast page)
    id: str | None = None
    items: list[KpiItem]


class ChartBlock(BaseModel):
    type: Literal["chart"] = "chart"
    id: str
    chart_id: str | None = None
    title: str
    subtitle: str | None = None
    figure: dict[str, Any]
    alt_figures: dict[str, dict[str, Any]] | None = None
    caption: str | None = None
    note: str | None = None
    height: int | None = None

    @model_validator(mode="after")
    def _sync_chart_fields(self) -> ChartBlock:
        if not self.chart_id:
            self.chart_id = self.id
        if self.caption is None and self.note is not None:
            self.caption = self.note
        if self.alt_figures and isinstance(self.figure, dict) and "variants" not in self.figure:
            variants: dict[str, Any] = {
                "default": {
                    "data": self.figure.get("data", []),
                    "layout": self.figure.get("layout", {}),
                }
            }
            variants.update(self.alt_figures)
            self.figure = {**self.figure, "variants": variants}
        return self


class TableColumn(BaseModel):
    key: str
    label: str
    align: Literal["left", "right", "center"] = "left"
    format: str | None = None


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    id: str
    table_id: str | None = None
    title: str
    subtitle: str | None = None
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
    caption: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _sync_table_fields(self) -> TableBlock:
        if not self.table_id:
            self.table_id = self.id
        if self.caption is None and self.note is not None:
            self.caption = self.note
        return self


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    tone: Literal["info", "warn", "success", "danger", "good", "bad"] = "info"
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
