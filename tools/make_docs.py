"""
Builds the project's document collection (PDFs) from tire_data.json.

Each PDF is compiled by the team from public sources (listed inside every PDF),
so the chatbot can cite them by file and page.

Run:  python tools/make_docs.py
Needs: pip install reportlab
"""
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = json.loads((ROOT / "tire_data.json").read_text(encoding="utf-8"))
CARS, SOURCES, LOAD, SPEED = DATA["CARS"], DATA["SOURCES"], DATA["LOAD"], DATA["SPEED"]

SLATE = colors.HexColor("#566d8f")
styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=20, leading=24, textColor=colors.HexColor("#1f2328"), spaceAfter=4)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13.5, leading=17, textColor=SLATE, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14.5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=12, textColor=colors.HexColor("#5f6368"))
CREDIT = "Compiled by the Tire Size AI team (5602203 Generative AI, BAScii) from the public sources listed at the end of this document. Always check the tire placard on the driver's door before buying tires."


# ---------------- tire math (same as the chatbot) ----------------
def parse(s):
    m = re.search(r"(\d{3})/(\d{2})R(\d{2})", s)
    return {"w": int(m[1]), "ar": int(m[2]), "rim": int(m[3]), "raw": m[0]}


def diameter(t):
    return t["rim"] * 25.4 + 2 * t["w"] * t["ar"] / 100


def plus_sizes(base, rims):
    D, res = diameter(base), []
    for rim in rims:
        for w in range(165, 300, 10):
            if w < base["w"] - 10 or w > base["w"] + 30:
                continue
            for ar in range(30, 85, 5):
                t = {"w": w, "ar": ar, "rim": rim, "raw": f"{w}/{ar}R{rim}"}
                d = (diameter(t) - D) / D * 100
                if abs(d) <= 3:
                    res.append((t, d))
    out, per = [], {}
    for t, d in sorted(res, key=lambda x: abs(x[1])):
        if per.get(t["rim"], 0) < 3:
            out.append((t, d))
            per[t["rim"]] = per.get(t["rim"], 0) + 1
    return sorted(out, key=lambda x: (x[0]["rim"], abs(x[1])))


def rim_width(w):
    lo, hi = round(w * 0.70 / 25.4 * 2) / 2, round(w * 0.90 / 25.4 * 2) / 2
    return f"{lo:g}J to {hi:g}J"


# ---------------- PDF helpers ----------------
def table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e4e8ee")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("TEXTCOLOR", (0, 0), (-1, 0), SLATE),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, SLATE)]
    t.setStyle(TableStyle(style))
    return t


def sources_block(keys):
    out = [Paragraph("Sources", H2)]
    for i, k in enumerate(dict.fromkeys(keys), 1):
        title, url = SOURCES[k]
        out.append(Paragraph(f"[{i}] {title}<br/><font color='#566d8f'>{url}</font>", SMALL))
    return out


def build(filename, title, story):
    doc = SimpleDocTemplate(str(DOCS / filename), pagesize=A4, title=title, author="Tire Size AI team",
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm)
    doc.build(story)
    print("  wrote", filename)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---------------- 1) one spec sheet per car ----------------
def car_sheet(c):
    sizes = [dict(s, t=parse(s["tire"])) for s in c["sizes"]]
    story = [Paragraph(f"{c['name']}, {c['years']}: Wheel and Tire Specifications", H1), Paragraph(CREDIT, SMALL)]

    story.append(Paragraph("Factory tire sizes", H2))
    for s in sizes:
        li = f" {s['li']}{s['sr']}" if s.get("li") else ""
        extra = ""
        if s.get("li"):
            extra = f" Load index {s['li']} means up to {LOAD[str(s['li'])]} kg per tire, and speed rating {s['sr']} means up to {SPEED[s['sr']]} km/h."
        et = f", offset {s['et']}" if s.get("et") else ""
        story.append(Paragraph(
            f"The {c['name']} {s['label']} version uses <b>{s['tire']}{li}</b> tires on {s['rim']} wheels{et}. "
            f"The overall tire diameter is {diameter(s['t']):.0f} mm.{extra}", BODY))
    rows = [["Version", "Tire", "Load / speed", "Wheel", "Offset", "Diameter"]]
    for s in sizes:
        rows.append([s["label"], s["tire"], f"{s['li']}{s['sr']}" if s.get("li") else "check placard",
                     s["rim"], s.get("et") or "-", f"{diameter(s['t']):.0f} mm"])
    story += [Spacer(1, 4), table(rows, [34 * mm, 28 * mm, 26 * mm, 26 * mm, 18 * mm, 22 * mm])]

    story.append(Paragraph("Wheel fitment", H2))
    story.append(Paragraph(
        f"Bolt pattern (PCD): <b>{c['pcd']}</b>. Center bore: <b>{c['cb']}</b>. Wheel nuts: {c['thread']}. "
        f"Tightening torque: {c['torque']}.", BODY))

    story.append(Paragraph("Notes", H2))
    story.append(Paragraph(c["note"], BODY))

    top = sorted(sizes, key=lambda s: -s["t"]["rim"])[0]
    opts = plus_sizes(top["t"], [top["t"]["rim"], top["t"]["rim"] + 1, top["t"]["rim"] + 2])
    story.append(Paragraph(f"Other tire sizes within ±3% of the factory {top['tire']}", H2))
    story.append(Paragraph(
        "These sizes keep the overall diameter within 3% of the factory tire, so the speedometer, ABS and "
        f"ride height stay close to stock. Use wheels with bolt pattern {c['pcd']} and center bore {c['cb']}"
        + (f", and tires with load index {top['li']} or higher." if top.get("li") else "."), BODY))
    rows = [["Wheel", "Tire size", "Diameter", "Change vs factory", "Suggested wheel width"]]
    for t, d in opts:
        rows.append([f'{t["rim"]}"', t["raw"], f"{diameter(t):.0f} mm", f"{d:+.2f}%", rim_width(t["w"])])
    story += [Spacer(1, 4), table(rows, [18 * mm, 30 * mm, 26 * mm, 34 * mm, 42 * mm])]

    story += sources_block(c["src"] + ["etrto"])
    fn = f"{slug(c['name'])}-specs.pdf"
    build(fn, f"{c['name']} wheel and tire specs", story)
    return fn, {"title": f"{c['name']} {c['years']}: wheel & tire specs", "category": "Car specs",
                "type": c["type"], "sources": [{"title": SOURCES[k][0], "url": SOURCES[k][1]} for k in c["src"]]}


# ---------------- 2) tire size basics ----------------
def basics():
    s = [Paragraph("Tire Size Basics: How to Read and Compare Tire Sizes", H1), Paragraph(CREDIT, SMALL)]
    s.append(Paragraph("Reading a tire size", H2))
    s.append(Paragraph(
        "A tire size such as <b>265/60R18 110H</b> has six parts. 265 is the tire width in millimetres. "
        "60 is the aspect ratio: the sidewall height is 60% of the width, so 159 mm. R means radial construction. "
        "18 is the wheel (rim) diameter in inches. 110 is the load index: the most weight one tire can carry, "
        "here 1,060 kg. H is the speed rating: the highest safe speed, here 210 km/h.", BODY))
    s.append(Paragraph("Overall diameter formula", H2))
    s.append(Paragraph(
        "Overall diameter (mm) = wheel diameter (inches) × 25.4 + 2 × width × aspect ratio / 100. "
        "Example: 265/60R18 = 18 × 25.4 + 2 × 265 × 0.60 = 457.2 + 318 = 775.2 mm. "
        "Circumference = π × diameter, so 265/60R18 travels about 2.435 m per turn, or about 411 turns per km.", BODY))
    s.append(Paragraph("The ±3% rule", H2))
    s.append(Paragraph(
        "When changing tire size, keep the new overall diameter within 3% of the factory size. "
        "Change (%) = (new diameter − factory diameter) / factory diameter × 100. "
        "Within ±3% is a good fit. 3% to 5% is risky. More than 5% is not recommended. "
        "A bigger tire makes the car go faster than the speedometer shows: real speed = shown speed × new diameter / factory diameter. "
        "For example, a tire 3.42% bigger means the car really goes 103.4 km/h when the speedometer shows 100 km/h. "
        "Big changes also affect ABS, stability control, ride height and fuel use.", BODY))
    rows = [["Example", "Factory", "New", "Change", "Verdict"],
            ["Honda City", "185/55R16", "195/50R16", "-1.39%", "Good fit"],
            ["Toyota Corolla Cross", "225/50R18", "225/45R18", "-3.30%", "Risky"],
            ["Isuzu D-Max", "265/60R18", "265/65R18", "+3.42%", "Risky"],
            ["Ford Ranger", "265/65R17", "265/70R17", "+3.41%", "Risky"]]
    s += [Spacer(1, 4), table(rows, [40 * mm, 28 * mm, 28 * mm, 22 * mm, 30 * mm])]
    s.append(Paragraph("Safety rules when changing tires", H2))
    s.append(Paragraph(
        "1) Never fit a tire with a lower load index than the factory tire. 2) Use a speed rating equal to or higher than the factory rating. "
        "3) A new wheel must have the same bolt pattern (PCD) as the car, and a center bore equal to or bigger than the hub (use hub rings if bigger). "
        "4) Rule of thumb for wheel width: about 70% to 90% of the tire width. 5) Much wider tires can rub the fender or suspension.", BODY))
    s.append(PageBreak())
    s.append(Paragraph("Load index table (ETRTO)", H2))
    items = sorted(((int(k), v) for k, v in LOAD.items()))
    rows = [["Load index", "Max kg", "Load index", "Max kg", "Load index", "Max kg"]]
    third = (len(items) + 2) // 3
    for i in range(third):
        row = []
        for col in range(3):
            j = i + col * third
            row += [str(items[j][0]), f"{items[j][1]:,}"] if j < len(items) else ["", ""]
        rows.append(row)
    s += [table(rows, [26 * mm, 24 * mm] * 3)]
    s.append(Paragraph("Speed rating table", H2))
    rows = [["Speed rating", "Max speed"]] + [[k, f"{v} km/h"] for k, v in SPEED.items()]
    s += [table(rows, [34 * mm, 34 * mm])]
    s += sources_block(["etrto", "nhtsa", "kones"])
    build("tire-size-basics.pdf", "Tire size basics", s)
    return "tire-size-basics.pdf", {"title": "Tire size basics: reading, ±3% rule, load & speed tables", "category": "Guides",
                                    "sources": [{"title": SOURCES[k][0], "url": SOURCES[k][1]} for k in ["etrto", "nhtsa", "kones"]]}


# ---------------- 3) overview + bolt patterns ----------------
def overview():
    s = [Paragraph("Popular Cars in Thailand: Factory Tire Sizes and Bolt Patterns", H1), Paragraph(CREDIT, SMALL)]
    s.append(Paragraph("All factory tire sizes", H2))
    rows = [["Car", "Years", "Factory tire sizes", "Bolt pattern", "Center bore"]]
    for c in CARS:
        rows.append([c["name"], c["years"], ", ".join(x["tire"] for x in c["sizes"]), c["pcd"], c["cb"]])
    s += [table(rows, [38 * mm, 28 * mm, 58 * mm, 22 * mm, 22 * mm])]
    s.append(Paragraph("Which cars can share wheels?", H2))
    s.append(Paragraph(
        "Wheels can only move between cars with the same bolt pattern. The wheel's center bore must be the same as "
        "or bigger than the hub; if it is bigger, use hub-centric rings. Offset and wheel size must also fit.", BODY))
    groups = {}
    for c in CARS:
        groups.setdefault(c["pcd"], []).append(c)
    for pcd, cs in groups.items():
        s.append(Paragraph(f"<b>{pcd}</b>: " + "; ".join(f"{c['name']} (center bore {c['cb']})" for c in cs) + ".", BODY))
    keys = [k for c in CARS for k in c["src"][:1]]
    s += sources_block(keys)
    build("thai-cars-overview.pdf", "Thai cars tire overview", s)
    return "thai-cars-overview.pdf", {"title": "Overview: factory sizes & bolt patterns for all cars", "category": "Guides",
                                      "sources": [{"title": SOURCES[k][0], "url": SOURCES[k][1]} for k in keys]}


# ---------------- downloadable public PDFs (fetched by download_docs.py) ----------------
EXTERNAL = {
    "continental-etrto-databook.pdf": {"title": "Continental Technical Databook (ETRTO tire data)", "category": "Standards & research",
                                       "url": SOURCES["etrto"][1]},
    "kones-2015-tire-diameter-fuel.pdf": {"title": "Gołębiewski (2015): tire diameter and fuel use, Journal of KONES", "category": "Standards & research",
                                          "url": SOURCES["kones"][1]},
    "nhtsa-tire-safety.pdf": {"title": "NHTSA: Tire Safety, Everything Rides On It", "category": "Standards & research",
                              "url": SOURCES["nhtsa"][1]},
}

if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    manifest = {}
    for fn, meta in [basics(), overview()] + [car_sheet(c) for c in CARS]:
        manifest[fn] = meta
    for fn, meta in EXTERNAL.items():
        manifest[fn] = {"title": meta["title"], "category": meta["category"], "download": meta["url"],
                        "sources": [{"title": meta["title"], "url": meta["url"]}]}
    (DOCS / "sources.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done: {len(manifest)} documents in the collection ({len(EXTERNAL)} to download with download_docs.py)")
