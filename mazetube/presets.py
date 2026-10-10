"""Contents presets: what the tube should hold.

Each entry is (label, diameter_mm, length_mm[, note]).  The sizes are typical
nominal dimensions of the object itself - the app adds the fit slack.  Real
bottles in particular vary by maker, so measure yours if the fit matters.
"""

from __future__ import annotations

# Banknotes: a roll is as long as the note's short side; a flat stack needs a
# bore as wide as the note's short side (plus the stack thickness, ~100 notes).
PRESETS = [
    ("Small gifts", [
        ("Small gift (default)", 30, 60),
        ("Ring / jewellery", 25, 30),
        ("Gift card / credit card", 56, 88, "Card 85.6 × 54 mm standing on its short edge"),
        ("Watch (on cushion)", 60, 75),
    ]),
    ("Cash", [
        ("AUD – rolled notes", 30, 65, "All AUD notes are 65 mm tall"),
        ("AUD – flat stack (up to $100)", 67, 158),
        ("USD – rolled notes", 30, 67, "US notes are 66.3 × 156 mm"),
        ("USD – flat stack", 68, 156),
        ("EUR – rolled notes (up to €200)", 30, 77),
        ("EUR – flat stack (up to €200)", 78, 153),
        ("GBP – rolled notes (up to £50)", 30, 77),
        ("GBP – flat stack (up to £50)", 78, 146),
        ("NZD – rolled notes (up to $100)", 30, 74),
        ("CAD – rolled notes", 30, 70),
        ("JPY – rolled notes (up to ¥10,000)", 30, 76),
    ]),
    ("Cans", [
        ("Can 250 ml slim (energy drink)", 53, 135),
        ("Can 330 ml standard (Coke)", 66, 115),
        ("Can 330 ml sleek", 58, 146),
        ("Can 355 ml / 12 oz (US)", 66, 123),
        ("Can 375 ml (Australia)", 66, 130),
        ("Can 440 ml", 66, 150),
        ("Can 500 ml", 66, 168),
    ]),
    ("Bottles", [
        ("Wine 750 ml – Bordeaux (straight sides)", 77, 300),
        ("Wine 750 ml – Burgundy / Pinot", 85, 300),
        ("Champagne / sparkling 750 ml", 90, 320),
        ("Wine half bottle 375 ml", 61, 240),
        ("Wine magnum 1.5 L", 105, 350),
        ("Beer bottle 330 ml (longneck)", 62, 230),
        ("Beer stubby 375 ml (Australia)", 70, 180),
        ("Spirits 700 ml (typical round)", 85, 290),
    ]),
    ("Paper & pens", [
        ("Rolled A4 – short edge (210 mm)", 30, 210),
        ("Rolled A4 – long edge / A3 (297 mm)", 35, 297),
        ("Rolled US Letter (8.5 in)", 30, 216),
        ("Pens / pencils", 30, 190),
    ]),
    ("Sport", [
        ("Golf balls × 3", 43, 128),
        ("Tennis ball", 68, 68),
        ("Tennis balls × 3", 68, 204),
    ]),
]


def all_presets():
    """Flat list of (category, label, diameter, length, note)."""
    out = []
    for cat, items in PRESETS:
        for it in items:
            out.append((cat, it[0], float(it[1]), float(it[2]), it[3] if len(it) > 3 else ""))
    return out


def slug(label: str) -> str:
    """File-name friendly version of a preset label."""
    keep = "".join(ch.lower() if ch.isalnum() else "_" for ch in label)
    while "__" in keep:
        keep = keep.replace("__", "_")
    return keep.strip("_")[:40]
