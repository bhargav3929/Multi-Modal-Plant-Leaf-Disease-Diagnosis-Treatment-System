"""Crops the grower can pick, and the leaf diseases the AI chooses from for each.

Telling the vision model the crop, and giving it that crop's known diseases, stops it guessing the wrong plant
("rose", "coffee") and naming vague diseases ("leaf blight"). The lists follow the PlantVillage classes plus the
common leaf diseases of major Indian crops.
"""
from typing import Dict, List

CROP_DISEASES: Dict[str, List[str]] = {
    "Tomato": ["Early blight", "Late blight", "Septoria leaf spot", "Bacterial spot", "Leaf mold", "Target spot",
               "Tomato yellow leaf curl virus", "Tomato mosaic virus", "Spider mite damage", "Powdery mildew"],
    "Potato": ["Early blight", "Late blight", "Potato leafroll virus", "Potato virus Y (mosaic)"],
    "Corn (maize)": ["Common rust", "Southern rust", "Northern leaf blight", "Gray leaf spot", "Fall armyworm damage"],
    "Rice": ["Blast", "Brown spot", "Bacterial leaf blight", "Sheath blight", "Tungro virus"],
    "Wheat": ["Leaf (brown) rust", "Yellow (stripe) rust", "Stem rust", "Powdery mildew", "Spot blotch"],
    "Cotton": ["Bacterial blight", "Alternaria leaf spot", "Cotton leaf curl virus", "Grey mildew"],
    "Chilli / Pepper": ["Bacterial leaf spot", "Cercospora leaf spot", "Powdery mildew", "Anthracnose", "Leaf curl virus"],
    "Cucumber / Squash / Pumpkin": ["Powdery mildew", "Downy mildew", "Anthracnose", "Angular leaf spot", "Mosaic virus"],
    "Groundnut": ["Early leaf spot", "Late leaf spot", "Rust"],
    "Soybean": ["Rust", "Frog-eye leaf spot", "Bacterial pustule", "Yellow mosaic virus"],
    "Apple": ["Apple scab", "Cedar apple rust", "Black rot (frog-eye leaf spot)", "Powdery mildew", "Alternaria leaf blotch"],
    "Grape": ["Black rot", "Esca (black measles)", "Leaf blight (Isariopsis leaf spot)", "Downy mildew", "Powdery mildew"],
    "Mango": ["Anthracnose", "Powdery mildew", "Bacterial canker", "Sooty mould"],
    "Banana": ["Sigatoka leaf spot", "Fusarium (Panama) wilt", "Bunchy top virus"],
    "Citrus": ["Citrus canker", "Citrus greening (HLB)", "Melanose"],
    "Strawberry": ["Leaf scorch", "Leaf spot", "Powdery mildew"],
    "Cherry": ["Powdery mildew", "Cherry leaf spot"],
    "Peach": ["Bacterial spot", "Peach leaf curl"],
}


def canonical(crop: str) -> str:
    """Return the listed crop name matching `crop` (case-insensitive), or '' if it isn't one of ours."""
    wanted = (crop or "").strip().lower()
    return next((name for name in CROP_DISEASES if name.lower() == wanted), "")


def instruction(crop: str) -> str:
    """Extra text for the AI when the grower picked a listed crop; '' otherwise."""
    name = canonical(crop)
    if not name:
        return ""
    options = "; ".join(CROP_DISEASES[name] + ["Healthy"])
    return (f" The grower says this is a {name} leaf: trust that and set crop to \"{name}\". "
            f"Choose the disease from this list: {options}. If no listed disease clearly fits, pick the closest one "
            f"and give a lower confidence. Answer \"Healthy\" only if you see no symptoms at all.")
