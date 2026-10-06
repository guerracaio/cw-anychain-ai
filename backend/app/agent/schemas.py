"""Final-answer contract between the model and the harness."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SUBMIT_TOOL = "submit_analysis"

EVIDENCE_IDS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Ids from the evidence list or from tool results. Never invent ids.",
}
CITED_ITEM = {
    "type": "object",
    "properties": {"description": {"type": "string"}, "evidence_ids": EVIDENCE_IDS},
    "required": ["description", "evidence_ids"],
}
SUBMIT_PARAMETERS = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-5 sentences explaining what happened, in the requested language.",
        },
        "findings": {
            "type": "array",
            "description": "Key points, each labeled by how it is known.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["observed", "decoded", "state", "source", "inference"],
                    },
                    "statement": {"type": "string"},
                    "evidence_ids": EVIDENCE_IDS,
                },
                "required": ["kind", "statement", "evidence_ids"],
            },
        },
        "likely_causes": {
            "type": "array",
            "description": "Only for failures or anomalies: hypotheses, not confirmed facts.",
            "items": CITED_ITEM,
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "security_notes": {
            "type": "array",
            "description": "Patterns that warrant review. Do not claim vulnerabilities.",
            "items": CITED_ITEM,
        },
        "uncertainties": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What could not be determined, why, and what data would be needed.",
        },
    },
    "required": [
        "summary",
        "findings",
        "likely_causes",
        "next_steps",
        "security_notes",
        "uncertainties",
    ],
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CitedItem(Strict):
    description: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class SubmittedFinding(Strict):
    kind: Literal["observed", "decoded", "state", "source", "inference"]
    statement: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class SubmittedAnalysis(Strict):
    summary: str = Field(min_length=1, max_length=3000)
    findings: list[SubmittedFinding] = Field(default_factory=list, max_length=20)
    likely_causes: list[CitedItem] = Field(default_factory=list, max_length=10)
    next_steps: list[str] = Field(default_factory=list, max_length=10)
    security_notes: list[CitedItem] = Field(default_factory=list, max_length=10)
    uncertainties: list[str] = Field(default_factory=list, max_length=15)
