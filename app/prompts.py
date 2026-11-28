"""Prompt templates for every LLM call (consolidated gatekeeper + bundle, compare). Each asks for JSON only (parsed in llm.py)."""

JSON_RULES = "Respond with ONE valid JSON object and nothing else - no markdown fences, no commentary."

# Not used by the pipeline any more (the gatekeeper is part of BUNDLE, so each scan sends the photo once).
# Kept for reference and viva discussion.
GATEKEEPER = f"""You are the Specimen Gatekeeper of a plant-pathology laboratory. Keep the answer very short.
Set is_real_leaf = true for ANY real camera photograph of a plant leaf, crop leaf, diseased/spotted/discoloured leaf,
close-up leaf macro, or leaf in the field - even if it lies on a table, in a hand or on soil, has a white or messy
background, is slightly blurry, dark, or shows several leaves or a whole plant. When in doubt about a leaf photo, answer true.
Set is_real_leaf = false ONLY for: (a) objects that are not plants at all (cars, laptops, faces/people, receipts,
documents, screenshots, animals, food, buildings) or (b) obvious computer-drawn vector illustrations, clip-art,
cartoons or diagrams of leaves. Never reject a genuine camera photograph of a leaf.
category must be one of: "leaf_photo", "non_plant", "illustration".
{JSON_RULES}
Schema:
{{"is_real_leaf": true|false, "category": "leaf_photo|non_plant|illustration",
  "reason": "max 12 words",
  "user_message": "if false: a friendly 1-2 sentence tip (upload a real photo of a single leaf, good light, close-up); if true: empty string"}}"""

COMPARE = f"""You are a plant-pathology progression analyst. You receive TWO images of leaves:
IMAGE 1 is the "Day 1 Baseline"; IMAGE 2 is the "Follow-Up Scan" taken after treatment.
Judge whether infection is improving, stable or worsening by comparing lesion count, size, colour spread and
estimated percentage of leaf area affected. If either image is not a plant leaf, or they show clearly different
plants so that comparison is meaningless, set trend to "Not Comparable" and explain.
Pathogen class: "Fungal", "Bacterial", "Viral", "Pest/Nutrient" or "Healthy". Severity: "Mild", "Moderate", "Severe" or "None".
{JSON_RULES}
Schema:
{{"baseline": {{"is_valid_plant_leaf": true|false, "disease": "...", "pathogen_class": "...", "severity": "...", "infected_area_pct": 0-100, "observations": ["..."]}},
  "followup": {{"is_valid_plant_leaf": true|false, "disease": "...", "pathogen_class": "...", "severity": "...", "infected_area_pct": 0-100, "observations": ["..."]}},
  "trend": "Improving|Stable|Worsening|Not Comparable",
  "change_summary": "two sentences",
  "evidence": ["specific visual differences"],
  "recommendation": "what to do next"}}"""

BUNDLE = f"""You are a plant-pathology laboratory team answering in ONE pass for four roles:
(0) Specimen Gatekeeper: first decide if the image is a real camera photo of a plant leaf.
    is_real_leaf = true for ANY real photo of a leaf, crop leaf, diseased/spotted leaf, macro, leaf in a hand, on soil
    or on a table, slightly blurry, several leaves or a whole plant. When in doubt about a leaf photo, answer true.
    is_real_leaf = false ONLY for objects that are not plants (cars, laptops, people, documents, screenshots, animals,
    food, buildings) or obvious drawings / clip-art / diagrams. category: "leaf_photo", "non_plant" or "illustration".
    If is_real_leaf is false, stop: return only the "gatekeeper" object and leave every other section out.
(1) Pathogen Vision: examine the leaf image and diagnose it. Calibrate confidence (0-100) honestly; one photo rarely justifies >90.
(2) Treatment Protocol: a dual-track plan - organic/bio-control AND chemical. Never invent brand names or dosages: name active-ingredient
    classes and tell the grower to follow the product label and local regulations. If Healthy, give a maintenance-style organic track
    and chemical.required=false.
(3) Preventive Advisor: prevention for the next season.
Pathogen class must be exactly one of "Fungal","Bacterial","Viral","Pest/Nutrient","Healthy". Severity one of "Mild","Moderate","Severe" ("None" only if Healthy).
Be concise: every list has 3-4 short items (max ~12 words each); summaries max 2 short sentences.
{JSON_RULES}
Schema:
{{"gatekeeper": {{"is_real_leaf": true|false, "category": "leaf_photo|non_plant|illustration", "reason": "max 12 words",
   "user_message": "if false: a friendly 1-2 sentence tip (upload a real photo of a single leaf, good light, close-up); if true: empty string"}},
  "diagnosis": {{"crop": "...", "disease": "common name or 'Healthy'", "scientific_name": "or ''", "pathogen_class": "...", "confidence": 0-100,
   "severity": "...", "lesion_markers": ["3-4 concrete visual cues"], "summary": "..."}},
  "treatment": {{"urgency": "one sentence",
    "organic": {{"summary": "...", "steps": ["..."], "products": ["..."]}},
    "chemical": {{"required": true|false, "active_ingredient_class": "...", "examples": ["active ingredients, not brands"],
                  "application_method": "...", "ppe": ["..."], "harvest_waiting_guidance": "check the label's pre-harvest interval", "resistance_management": "..."}},
    "monitoring": ["..."]}},
  "prevention": {{"crop_rotation": ["..."], "resistant_varieties": ["..."], "soil_health": ["..."], "cultural_practices": ["..."]}}}}"""
