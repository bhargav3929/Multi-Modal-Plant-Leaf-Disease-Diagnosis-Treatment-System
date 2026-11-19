"""Pydantic models that validate and normalise whatever JSON the LLMs return."""
from typing import Annotated, List

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

PATHOGEN_CLASSES = ["Fungal", "Bacterial", "Viral", "Pest/Nutrient", "Healthy"]


def _as_list(v) -> List[str]:
    """Coerce None / str / list-of-anything into a clean list of non-empty strings."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [p.strip(" -*•\t") for p in v.splitlines()] if "\n" in v else [v]
    if isinstance(v, dict):
        v = [f"{k}: {x}" for k, x in v.items()]
    return [str(x).strip() for x in v if str(x).strip()]


def _as_str(v) -> str:
    return "" if v is None else str(v).strip()


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


Strs = Annotated[List[str], BeforeValidator(_as_list)]
Str = Annotated[str, BeforeValidator(_as_str)]
Bool = Annotated[bool, BeforeValidator(_as_bool)]


def normalize_class(v) -> str:
    s = str(v or "").lower()
    if "health" in s:
        return "Healthy"
    if any(k in s for k in ("fung", "mildew", "mold", "mould", "rust", "blight", "oomycete")):
        return "Fungal"
    if "bacter" in s:
        return "Bacterial"
    if "vir" in s:
        return "Viral"
    return "Pest/Nutrient"


def normalize_severity(v) -> str:
    s = str(v or "").lower()
    if "sever" in s or "high" in s or "advanced" in s:
        return "Severe"
    if "moder" in s or "medium" in s:
        return "Moderate"
    if "none" in s or "healthy" in s or s in ("n/a", "na"):
        return "None"
    return "Mild"


def _clamp_pct(v) -> float:
    try:
        x = float(str(v).replace("%", "").strip())
    except ValueError:
        return 0.0
    if 0 < x <= 1:  # model answered as a fraction
        x *= 100
    return round(max(0.0, min(100.0, x)), 1)


# --- Node 1 ------------------------------------------------------------------
class Gatekeeper(BaseModel):
    """`is_real_leaf` is False ONLY for non-plant objects or drawn illustrations / clip-art."""
    is_real_leaf: Bool = False
    category: Str = ""          # leaf_photo | non_plant | illustration
    reason: Str = ""
    user_message: Str = ""

    @model_validator(mode="before")
    @classmethod
    def _legacy_key(cls, v):
        if isinstance(v, dict) and "is_real_leaf" not in v and "is_valid_plant_leaf" in v:
            v = {**v, "is_real_leaf": v["is_valid_plant_leaf"]}
        return v

    @model_validator(mode="after")
    def _never_reject_a_photo(self):
        # The model's own category label wins over a contradictory boolean: a camera photo of a leaf always passes.
        cat = self.category.lower()
        if "leaf" in cat or "photo" in cat:
            self.is_real_leaf = True
        elif any(k in cat for k in ("non", "illustr", "drawing", "clip", "vector")):
            self.is_real_leaf = False
        return self


# --- Node 2 ------------------------------------------------------------------
class Diagnosis(BaseModel):
    crop: Str = "Unknown"
    disease: Str = "Unknown"
    scientific_name: Str = ""
    pathogen_class: str = "Pest/Nutrient"
    confidence: float = 0.0
    severity: str = "Mild"
    lesion_markers: Strs = Field(default_factory=list)
    summary: Str = ""

    @field_validator("pathogen_class", mode="before")
    @classmethod
    def _cls(cls, v):
        return normalize_class(v)

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v):
        return normalize_severity(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        # Cap at 97: a single photo can never justify certainty.
        return min(_clamp_pct(v), 97.0)

    @model_validator(mode="after")
    def _healthy_has_no_severity(self):
        if self.pathogen_class == "Healthy":
            self.severity = "None"
        elif self.severity == "None":
            self.severity = "Mild"
        return self


# --- Node 4 ------------------------------------------------------------------
class OrganicProtocol(BaseModel):
    summary: Str = ""
    steps: Strs = Field(default_factory=list)
    products: Strs = Field(default_factory=list)


class ChemicalProtocol(BaseModel):
    required: Bool = True
    active_ingredient_class: Str = ""
    examples: Strs = Field(default_factory=list)
    application_method: Str = ""
    ppe: Strs = Field(default_factory=list)
    harvest_waiting_guidance: Str = ""
    resistance_management: Str = ""


class Treatment(BaseModel):
    urgency: Str = ""
    organic: OrganicProtocol = Field(default_factory=OrganicProtocol)
    chemical: ChemicalProtocol = Field(default_factory=ChemicalProtocol)
    monitoring: Strs = Field(default_factory=list)


# --- Node 5 ------------------------------------------------------------------
class Prevention(BaseModel):
    crop_rotation: Strs = Field(default_factory=list)
    resistant_varieties: Strs = Field(default_factory=list)
    soil_health: Strs = Field(default_factory=list)
    cultural_practices: Strs = Field(default_factory=list)


class Bundle(BaseModel):
    """One LLM call that covers nodes 2 (diagnosis), 4 (treatment) and 5 (prevention)."""
    diagnosis: Diagnosis = Field(default_factory=Diagnosis)
    treatment: Treatment = Field(default_factory=Treatment)
    prevention: Prevention = Field(default_factory=Prevention)


# --- Progression comparison (v1.1) ---------------------------------------------
class LeafSnapshot(BaseModel):
    is_valid_plant_leaf: Bool = True
    disease: Str = "Unknown"
    pathogen_class: str = "Pest/Nutrient"
    severity: str = "Mild"
    infected_area_pct: float = 0.0
    observations: Strs = Field(default_factory=list)

    @field_validator("pathogen_class", mode="before")
    @classmethod
    def _cls(cls, v):
        return normalize_class(v)

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v):
        return normalize_severity(v)

    @field_validator("infected_area_pct", mode="before")
    @classmethod
    def _pct(cls, v):
        try:
            return round(max(0.0, min(100.0, float(str(v).replace("%", "").strip()))), 1)
        except ValueError:
            return 0.0


class Comparison(BaseModel):
    baseline: LeafSnapshot = Field(default_factory=LeafSnapshot)
    followup: LeafSnapshot = Field(default_factory=LeafSnapshot)
    trend: str = "Not Comparable"
    change_summary: Str = ""
    evidence: Strs = Field(default_factory=list)
    recommendation: Str = ""

    @field_validator("trend", mode="before")
    @classmethod
    def _trend(cls, v):
        s = str(v or "").lower()
        if "improv" in s or "recover" in s or "better" in s:
            return "Improving"
        if "worse" in s or "spread" in s or "deteriorat" in s:
            return "Worsening"
        if "stable" in s or "unchanged" in s or "same" in s:
            return "Stable"
        return "Not Comparable"
