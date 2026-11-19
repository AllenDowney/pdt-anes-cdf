"""Compare every CDF variable in the HTML codebook with the Stata file (PublicDataToolkit Task 20).

Usage: python build/scan_codebook_vs_data.py . out.json

Writes two lists: codes in the data that the codebook's Valid and Missing
sections don't list (with counts by year, VCF0004), and variables whose
Sources list omits a year with valid data, or lists a year with no data.
It's a screen, not a verdict: variables with no Valid section (summary
indexes such as VCF0316) and codes given as words or open ranges show up
as false positives. The evidence for metadata/corrections.jsonl came from it.
"""
import sys, re, gzip, json, importlib.util, pandas as pd
from pathlib import Path
repo = Path(sys.argv[1]); out = sys.argv[2]
spec = importlib.util.spec_from_file_location("bs", repo / "build/build_slice.py"); bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)
cb = bs.parse_codebook(repo / "data/raw/anes_cdf_codebook_20260205.html")
with gzip.open(repo / "data/raw/anes_timeseries_cdf_stata_20260205.dta.gz") as f:
    df = pd.read_stata(f, convert_categoricals=False)
CODE = re.compile(r"^\s*(-?\d+)(?:\s*-\s*(-?\d+))?\.", re.M)
def codes(text):
    s = set()
    for a, b in CODE.findall(text or ""):
        a = int(a); b = int(b) if b else a
        if b - a <= 1000: s.update(range(a, b + 1))
    return s
res = {"undocumented": [], "sources": []}
for name, sec in cb.items():
    if name not in df or not pd.api.types.is_numeric_dtype(df[name]): continue
    valid, missing = codes(sec.get("valid")), codes(sec.get("missing"))
    col = df[name]; yr = df["VCF0004"]
    if not valid and not missing: continue
    vals = col.dropna()
    if (vals % 1 != 0).any(): continue
    und = sorted(set(vals.astype(int)) - valid - missing)
    for c in und:
        by = yr[col == c].astype(int).value_counts().sort_index().to_dict()
        res["undocumented"].append({"name": name, "code": c, "by_year": by})
    src_years = {int(y) for y in re.findall(r"^\s*((?:19|20)\d\d)\s*:", sec.get("sources", ""), re.M)}
    vyears = set(yr[col.isin(valid)].astype(int)) if valid else set()
    extra = sorted(vyears - src_years); gone = sorted(src_years - set(yr[col.notna()].astype(int)))
    if src_years and (extra or gone):
        res["sources"].append({"name": name, "valid_not_in_sources": extra, "in_sources_no_data": gone})
json.dump(res, open(out, "w"), indent=1)
print(len(res["undocumented"]), "undocumented codes;", len(res["sources"]), "source mismatches")
