"""Build the ANES CDF slice's metadata records from the HTML codebook and the Stata file.

Usage:
    python build/build_slice.py

Reads build/slice.yaml, data/raw/anes_cdf_codebook_20260205.html and
data/raw/anes_timeseries_cdf_stata_20260205.dta.gz. Writes
metadata/releases/<release>/variables.jsonl, metadata/derived/scale_signature.jsonl
and build/report.md.

Where each field comes from:
- question text, codes and labels (Valid and Missing sections), years (Sources),
  notes: the codebook
- variable label, which codes occur: the Stata file (public pandas API only)
- measure: build/slice.yaml (hand)
- missing types, wording by year, layout clean-up, phase, storage type, scale
  signatures: rules in this file

The variable-block regexes come from ExtractANES's extract_cdf_codebook.py.
Unlike that script, this one keeps the codebook's own valid/missing split,
types each missing code, and doesn't compute a valid range from listed codes.
"""

import collections
import html
import json
import re
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
META = ROOT / "metadata"

CODEBOOK = "anes-cdf-codebook-html"
STATA = "anes-cdf-stata"
RULE_VERSION = "0.1"
MISSING_RULE_VERSION = "0.2"  # anes_missing_types; 0.2 adds questionnaire versions (form B, short form, ...)

# ------------------------------------------------------- codebook blocks

VAR_HEADER_RE = re.compile(
    r"<table class=\"table table-condensed\s*\">"
    r"\s*<tbody><tr class=\"blocks header\">"
    r"\s*<td class=\"first\"[^>]*>\s*(VCF\d{4}[a-z]?)\s*</td>"
    r"\s*<td class=\"second\">\s*(.*?)\s*</td>",
    re.DOTALL | re.IGNORECASE,
)
SECTION_RE = re.compile(
    r"<div style=\"font-weight: bold; \">\s*(\w+)\s*</div>\s*"
    r"<div style=\"padding-left:70px[^\"]*\">(.*?)</div>",
    re.DOTALL | re.IGNORECASE,
)


def html_to_text(raw):
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_codebook(path):
    """Return {name: {section: raw text with the codebook's line breaks}}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    entries = {}
    for match in VAR_HEADER_RE.finditer(text):
        end = text.find("</table>", match.end())
        block = text[match.start():end]
        sections = {s.group(1).lower(): html_to_text(s.group(2)) for s in SECTION_RE.finditer(block)}
        entries[match.group(1)] = sections
    return entries


# --------------------------------------------------------------- rules

# Rule anes_text: the HTML keeps the PDF's line breaks, which are layout, not
# content. Join wrapped lines with single spaces (and non-breaking spaces
# become spaces). In question text, a line that is only a year or mode heading
# ("2000 TELEPHONE:", "ALL YEARS EXC. 2000 TELEPHONE,2002:") starts a new
# wording. In notes, a newline is kept before each "... NOTE:" heading.


def unwrap(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


QUESTION_HEADING = re.compile(r"^\s*((?:ALL YEARS|19\d\d|20\d\d)[^:\n]{0,60}):\s*$")
YEAR_LIST = re.compile(r"^(?:19|20)\d\d(?:\s*,\s*(?:19|20)\d\d)*$")
MODES = {"TELEPHONE": "telephone", "FTF": "face-to-face", "WEB": "web"}


def parse_questions(text):
    """Split question text into wordings by heading; return a list of question objects."""
    chunks, heading, lines = [], None, []
    for line in text.splitlines():
        m = QUESTION_HEADING.match(line)
        if m:
            if any(s.strip() for s in lines):
                chunks.append((heading, lines))
            heading, lines = m.group(1).strip(), []
        else:
            lines.append(line)
    if any(s.strip() for s in lines):
        chunks.append((heading, lines))

    questions = []
    for heading, lines in chunks:
        q = {"text": unwrap("\n".join(lines))}
        if heading:
            q["note"] = heading
            words = heading.split()
            modes = [MODES[w] for w in words if w in MODES]
            years = " ".join(w for w in words if w not in MODES)
            if "EXC" not in heading and YEAR_LIST.match(years.strip(" ,")):
                q["waves"] = [int(y) for y in re.findall(r"(?:19|20)\d\d", years)]
                if modes:
                    q["modes"] = modes
        questions.append(q)
    return questions


NOTE_HEADING = re.compile(r"\s*(GENERAL NOTE:|\b(?:19|20)\d\d(?:\s*[-,]\s*(?:19|20)\d\d)*\s+NOTE:)")


def clean_notes(text):
    return NOTE_HEADING.sub(lambda m: "\n" + m.group(1), unwrap(text)).strip()


# Codes in the Valid and Missing sections: "1. Favor", "-8. DK; depends (VOL)",
# "00-96. Degrees as coded", "INAP. question not used". Lines that don't start
# a code continue the previous one.
ENTRY = re.compile(r"^\s*(-?\d+\s*-\s*-?\d+|-?\d+|INAP|Inap)\.\s*(.*)$")


def parse_codes(text):
    """Return a list of (code token, label) in order."""
    entries = []
    for line in text.splitlines():
        m = ENTRY.match(line)
        if m:
            entries.append([m.group(1), [m.group(2)]])
        elif entries:
            entries[-1][1].append(line)
    out, inap_at = [], None
    for token, parts in entries:
        label = unwrap(" ".join(parts))
        if token.upper() == "INAP":
            if inap_at is not None:
                # "INAP." followed by "Inap. question not used" is one code; keep the text.
                if not out[inap_at][1]:
                    out[inap_at] = (out[inap_at][0], label)
                continue
            inap_at = len(out)
        out.append((token, label))
    return out


# Rule anes_missing_types: missing types from the parts of a missing code's
# label, split at ";". The codebook says the code is missing; this rule says
# why. "question not used" (the CDF's INAP) means not asked in that study.
MISSING_PATTERNS = [
    (re.compile(r"question not used", re.I), "not_asked"),
    (re.compile(r"\bno (pre|post)[ -]?(iw|data)\b|not administered", re.I), "not_asked"),
    # Version 0.2: the respondent got a questionnaire version without the item.
    (re.compile(r"\bform [A-Z]+\b|short[- ]form|abbrev\.|spanish language|telephone IW|"
                r"split versions: not asked|cross section \(", re.I), "not_asked"),
    (re.compile(r"\bDK\b|don'?t know", re.I), "dk"),
    (re.compile(r"\bRF\b|refused", re.I), "refused"),
    (re.compile(r"\bNA\b", re.I), "no_answer"),
    (re.compile(r"\bINAP\b|inapplicable", re.I), "inap"),
    (re.compile(r"did not vote|not for president", re.I), "inap"),
]


def missing_types(label):
    """Return (types, parts no pattern matched)."""
    types, unmatched = [], []
    for part in re.split(r";", label):
        hits = [t for pat, t in MISSING_PATTERNS if pat.search(part)]
        if hits:
            types.extend(t for t in hits if t not in types)
        elif part.strip():
            unmatched.append(part.strip())
    return types, unmatched


# Rule anes_phase: a note saying "This is a pre var." or "This is a post var."
PHASE = re.compile(r"This is a (pre|post) var\b", re.I)

# Rule scale_signature_from_valid_labels.
SCALE_SIGNATURES = {
    ("Favor", "Oppose"): "favor_oppose",
}


def scale_signature(record):
    rng = record.get("valid_range", {})
    if "degree" in rng.get("note", "").lower():
        return "thermometer"
    labels = tuple(v["label"] for v in record.get("values", []) if v["kind"] == "valid")
    return SCALE_SIGNATURES.get(labels)


# ---------------------------------------------------------------- build


def load_slice():
    spec = yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())
    names = [n for group in spec["variables"].values() for n in group]
    assert len(names) == len(set(names)), "duplicate names in slice.yaml"
    measure = {n: m for m, ns in spec["measure"].items() for n in ns}
    return spec, names, measure


def load_stata(names):
    path = RAW / "anes_timeseries_cdf_stata_20260205.dta.gz"
    with pd.io.stata.StataReader(path) as reader:
        var_labels = reader.variable_labels()
    frame = pd.read_stata(path, columns=names, convert_missing=True, convert_categoricals=False)
    return var_labels, frame


def as_code(token):
    return int(token)


def build_variable(name, spec, measure, sections, var_labels, frame):
    report = []
    hand = {"origin": "hand", "author": spec["author"], "date": spec["date"]}
    col = frame[name]
    is_missing_value = col.map(lambda v: getattr(v, "string", None) is not None)
    numbers = pd.to_numeric(col[~is_missing_value], errors="coerce").dropna()
    occurring = collections.Counter(numbers)
    n_system_missing = int(is_missing_value.sum())

    record = {"dataset": "anes-cdf", "release": spec["release"], "name": name}
    if var_labels.get(name):
        record["label"] = var_labels[name]
    if sections.get("question"):
        record["questions"] = parse_questions(sections["question"])
    if len(numbers):
        record["storage_type"] = "integer" if (numbers % 1 == 0).all() else "float"
    if name in measure:
        record["measure"] = measure[name]

    values, documented, rng = [], set(), None
    for token, label in parse_codes(sections.get("valid", "")):
        if "-" in token.lstrip("-") and re.search(r"\d\s*-\s*-?\d", token):
            lo, hi = (int(x) for x in re.split(r"(?<=\d)\s*-\s*", token))
            rng = {"min": lo, "max": hi, "note": label}
            continue
        code = as_code(token)
        documented.add(code)
        values.append({"code": code, "label": label, "kind": "valid"})
    for token, label in parse_codes(sections.get("missing", "")):
        code = "." if token.upper() == "INAP" else as_code(token)
        types, unmatched = missing_types(label)  # typed before the INAP prefix is added
        if token.upper() == "INAP":
            label = f"INAP. {label}"
        documented.add(code)
        origin = {"origin": "rule", "rule": "anes_missing_types", "rule_version": MISSING_RULE_VERSION,
                  "source": CODEBOOK, "location": f"{name}, Missing",
                  "note": "Listed under Missing in the codebook; types from the parts of its label."}
        if not types:
            types = ["other"]
            report.append(f"`{name}` code {code}: no missing type recognized in {label!r}; typed other")
        elif unmatched:
            report.append(f"`{name}` code {code}: label parts not typed: {unmatched}")
        values.append({"code": code, "label": label, "kind": "missing", "missing_types": types, "origin": origin})

    # C4: codes in the data that the codebook doesn't list.
    for code, count in sorted(occurring.items()):
        code = int(code) if float(code).is_integer() else code
        in_range = rng and rng["min"] <= code <= rng["max"]
        if code not in documented and not in_range:
            if measure.get(name) in {"identifier", "weight"} or (rng is None and not values):
                continue
            values.append({"code": code, "label": "", "kind": "unresolved",
                           "origin": {"origin": "data_file", "source": STATA,
                                      "note": f"Occurs {count} times in the data; not listed in the codebook."}})
            report.append(f"`{name}` code {code}: occurs {count} times, not in the codebook; marked unresolved")
    if n_system_missing and "." not in documented and values:
        report.append(f"`{name}`: {n_system_missing} system-missing values, but no INAP entry in the codebook")

    if values:
        record["values"] = values
    if rng:
        record["valid_range"] = rng
    elif measure.get(name) in {"continuous", "weight", "identifier"} and len(numbers) and not values:
        record["valid_range"] = {"min": float(numbers.min()), "max": float(numbers.max())}
        record["valid_range"] = {k: int(v) if v.is_integer() else v for k, v in record["valid_range"].items()}

    years = sorted({int(y) for y in re.findall(r"\b((?:19|20)\d{2}):", sections.get("sources", ""))})
    phase = PHASE.search(sections.get("notes", ""))
    if years:
        record["availability"] = [{"wave": y, **({"phase": phase.group(1).lower()} if phase else {})} for y in years]
    if sections.get("notes"):
        record["notes"] = clean_notes(sections["notes"])
    record["source_location"] = {"source": CODEBOOK, "anchor": name}

    fields = {"label": {"origin": "data_file", "source": STATA}}
    if "questions" in record:
        fields["questions"] = {"origin": "rule", "rule": "anes_text", "rule_version": RULE_VERSION, "source": CODEBOOK,
                               "note": "Codebook text with layout line breaks removed; split into wordings at "
                                       "year or mode headings."}
    if "notes" in record:
        fields["notes"] = {"origin": "rule", "rule": "anes_text", "rule_version": RULE_VERSION, "source": CODEBOOK,
                           "note": "Codebook notes with layout line breaks removed."}
    if "storage_type" in record:
        fields["storage_type"] = {"origin": "rule", "rule": "storage_type_from_values", "rule_version": RULE_VERSION,
                                  "source": STATA, "note": "integer if every value in the data is whole, else float."}
    if "measure" in record:
        fields["measure"] = hand
    if "valid_range" in record and not rng:
        fields["valid_range"] = {"origin": "data_file", "source": STATA, "note": "Min and max of the values that occur."}
    if phase:
        fields["availability"] = {"origin": "rule", "rule": "anes_phase", "rule_version": RULE_VERSION,
                                  "source": CODEBOOK,
                                  "note": "Years from the Sources section; phase from 'This is a pre/post var' in the notes."}
    record["provenance"] = {"record": {"origin": "publisher", "source": CODEBOOK}, "fields": fields}
    return record, report


def check_availability(record, frame):
    """C5: years in the codebook's Sources vs years with valid answers in the data."""
    name = record["name"]
    missing_codes = {v["code"] for v in record.get("values", []) if v["kind"] != "valid"}
    col = frame[name]
    ok = col.map(lambda v: getattr(v, "string", None) is None and pd.notna(v) and v not in missing_codes)
    data_years = set(frame.loc[ok, "VCF0004"].astype(int))
    doc_years = {a["wave"] for a in record.get("availability", [])}
    if doc_years and data_years != doc_years:
        return [f"`{name}`: years with data but not in Sources {sorted(data_years - doc_years)}; "
                f"in Sources but no data {sorted(doc_years - data_years)}"]
    return []


def main():
    spec, names, measure = load_slice()
    codebook = parse_codebook(RAW / "anes_cdf_codebook_20260205.html")
    var_labels, frame = load_stata(names)
    absent = [n for n in names if n not in codebook]

    records, notes, c5 = [], [], []
    for name in names:
        record, report = build_variable(name, spec, measure, codebook.get(name, {}), var_labels, frame)
        records.append(record)
        notes.extend(report)
        if name not in {"VCF0004", "VCF0006", "VCF0009z"}:
            c5.extend(check_availability(record, frame))

    write_jsonl(META / "releases" / spec["release"] / "variables.jsonl", records)
    signatures = [{"target": {"variable": r["name"]}, "property": "scale_signature", "value": s,
                   "rule": "scale_signature_from_valid_labels", "rule_version": RULE_VERSION}
                  for r in records if (s := scale_signature(r))]
    write_jsonl(META / "derived" / "scale_signature.jsonl", signatures)

    n_missing = collections.Counter(t for r in records for v in r.get("values", []) if v["kind"] == "missing"
                                    for t in v["missing_types"])
    lines = [
        f"# Build report: ANES CDF slice, release {spec['release']}",
        "",
        "Generated by `build/build_slice.py`. Do not edit.",
        "",
        f"- Variables: {len(records)}; not in the codebook: {absent or 'none'}",
        f"- With question text: {sum('questions' in r for r in records)}; "
        f"with more than one wording: {sum(len(r.get('questions', [])) > 1 for r in records)}",
        f"- With a valid range: {sum('valid_range' in r for r in records)}",
        f"- With a phase from the notes: {sum(any('phase' in a for a in r.get('availability', [])) for r in records)}",
        f"- Unresolved codes: {sum(v['kind'] == 'unresolved' for r in records for v in r.get('values', []))}",
        f"- Scale signatures: {len(signatures)}",
        "",
        "## Missing types assigned, by number of codes",
        "",
        "| Type | Codes |",
        "|---|---|",
        *[f"| `{t}` | {k} |" for t, k in sorted(n_missing.items())],
        "",
        "## Values and labels to look at",
        "",
        *([f"- {n}" for n in notes] or ["None."]),
        "",
        "## C5: years in the codebook vs the data",
        "",
        *([f"- {n}" for n in c5] or ["No differences."]),
        "",
    ]
    (ROOT / "build" / "report.md").write_text("\n".join(lines))
    print(f"{len(records)} variables, {len(signatures)} scale signatures; see build/report.md")


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
