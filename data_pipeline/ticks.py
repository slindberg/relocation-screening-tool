"""
Lyme disease risk by county.

The scored signal is the Lyme **incidence rate**: CDC reported Lyme case counts by
county (2023) divided by county population (2020 decennial), as cases/100k. This is
continuous (unlike a tick presence/absence flag) and is what actually matters for
relocation risk. Tick establishment (the Lyme vector's presence) is kept only as an
informational raw column, not scored.

All joins are on the 5-digit county FIPS. Layers degrade gracefully: if the rate
can't be computed (no county population), case-count percentile is used as a
fallback; the method is recorded in column_metadata.csv.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import requests

from . import config as C
from . import fetch


def _read_table(path) -> pd.DataFrame:
    """Read a CDC table whether it is .xlsx/.xls or .csv (all columns as str).
    CDC CSVs are not UTF-8 (county names like Mayagüez contain Latin-1 bytes), so
    fall back to latin-1 rather than crashing."""
    s = str(path).lower()
    if s.endswith((".xlsx", ".xls")):
        return pd.read_excel(path, dtype=str)
    try:
        return pd.read_csv(path, dtype=str)
    except UnicodeDecodeError:
        return pd.read_csv(path, dtype=str, encoding="latin-1")


def _latest_cases_col(cols) -> str | None:
    """Pick the most recent annual cases column from a CDC time-series file
    (Cases2001..cases2023). Falls back to any column containing 'case'."""
    years = {}
    for c in cols:
        m = re.fullmatch(r"cases?\s*_?(\d{4})", str(c).strip(), re.IGNORECASE)
        if m:
            years[int(m.group(1))] = c
    if years:
        return years[max(years)]
    return next((c for c in cols if "case" in str(c).lower()), None)


def _find_fips_col(cols) -> str | None:
    for c in cols:
        cl = str(c).lower()
        if "fips" in cl or cl in ("geoid", "countyfips", "county_fips"):
            return c
    return None


def _read_fips_sheet(path) -> pd.DataFrame | None:
    """Return the sheet that holds county-FIPS records, locating the real header row
    even when title/notes rows sit above it (the CDC tick workbook is laid out that
    way: cover sheets, then a data sheet whose header is one row below a long title)."""
    s = str(path).lower()
    if not s.endswith((".xlsx", ".xls")):
        return _read_table(path)
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        try:
            raw = xl.parse(sheet, dtype=str, header=None)
        except Exception:
            continue
        for i in range(min(20, len(raw))):
            row = raw.iloc[i].astype(str)
            if row.str.contains("fips", case=False, na=False).any():
                df = raw.iloc[i + 1:].copy()
                df.columns = [str(c).strip() for c in raw.iloc[i]]
                df = df.reset_index(drop=True)
                if _find_fips_col(df.columns) is not None:
                    return df
    return None


def _lyme_county_fips(ly: pd.DataFrame) -> pd.Series | None:
    """Build a 5-digit county FIPS from a CDC Lyme file, whether it has a single
    FIPS column or separate 2-digit state + 3-digit county code columns."""
    cols = {str(c).lower(): c for c in ly.columns}
    single = _find_fips_col(ly.columns)
    if single:
        return ly[single].astype(str).str.extract(r"(\d{4,5})")[0].str.zfill(5)
    st = next((cols[k] for k in cols if k in
               ("stcode", "statefp", "ste_code", "state_fips", "statefips")), None)
    cty = next((cols[k] for k in cols if k in
                ("ctycode", "countyfp", "cty_code", "county_fips", "countyfips")), None)
    if st and cty:
        s = ly[st].astype(str).str.extract(r"(\d+)")[0].str.zfill(2)
        c = ly[cty].astype(str).str.extract(r"(\d+)")[0].str.zfill(3)
        return s + c
    return None


def _county_population() -> pd.Series | None:
    """2020 decennial county population, indexed by 5-digit FIPS. Needs a Census key."""
    if not C.CENSUS_API_KEY:
        print("[lyme] CENSUS_API_KEY not set; cannot compute incidence rate "
              "(will fall back to case-count percentile)")
        return None
    try:
        r = requests.get(C.COUNTY_POP_URL + f"&key={C.CENSUS_API_KEY}", timeout=120)
        rows = r.json()
        cp = pd.DataFrame(rows[1:], columns=rows[0])
        cp["fips"] = cp["state"].str.zfill(2) + cp["county"].str.zfill(3)
        cp["pop"] = pd.to_numeric(cp[C.COUNTY_POP_VAR], errors="coerce")
        return cp.set_index("fips")["pop"]
    except Exception as exc:
        print(f"[lyme] county population unavailable ({exc})")
        return None


def attach_lyme(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["raw_lyme_incidence_per_100k"] = pd.NA
    df["raw_lyme_cases_2023"] = pd.NA
    df["raw_tick_established"] = pd.NA

    # ---- Lyme incidence rate (the scored signal) ----------------------------
    lyme_path = fetch.fetch_cdc_lyme()
    if lyme_path is not None:
        try:
            ly = _read_table(lyme_path)
            fips = _lyme_county_fips(ly)
            cases_col = _latest_cases_col(ly.columns)
            if fips is not None and cases_col:
                print(f"[lyme] using cases column '{cases_col}'")
                tmp = pd.DataFrame({
                    "_fips": fips.values,
                    "_cases": pd.to_numeric(ly[cases_col], errors="coerce"),
                }).dropna(subset=["_fips"])
                cases_by_fips = tmp.groupby("_fips")["_cases"].sum()
                # Counties absent from the file have no reported cases -> 0.
                cases = df["county_fips"].map(cases_by_fips).fillna(0.0)
                df["raw_lyme_cases_2023"] = cases

                cpop = _county_population()
                if cpop is None:
                    # Do NOT silently fall back to raw case counts: that is
                    # population-confounded and, with caching, would persist as if
                    # correct. Fail loudly instead.
                    raise RuntimeError(
                        "[lyme] county population unavailable — cannot compute the "
                        "incidence RATE. Set a valid CENSUS_API_KEY in .env (and "
                        "ensure network access), then re-run. Refusing to cache a "
                        "case-count fallback."
                    )
                pop = df["county_fips"].map(cpop)
                df["raw_lyme_incidence_per_100k"] = np.where(
                    pop > 0, cases / pop * 100000.0, np.nan)
                matched = int(df["raw_lyme_incidence_per_100k"].notna().sum())
                print(f"[lyme] incidence rate computed for {matched}/{len(df)} places "
                      f"(2023 cases / ACS 2023 county pop)")
            else:
                print(f"[lyme] could not find FIPS/cases columns; got {list(ly.columns)}")
        except Exception as exc:
            print(f"[lyme] could not parse Lyme file ({exc}); leaving null")

    # ---- Tick establishment (context only, not scored) ----------------------
    tick_path = fetch.fetch_cdc_ticks()
    if tick_path is not None:
        try:
            t = _read_fips_sheet(tick_path)
            fips = _find_fips_col(t.columns) if t is not None else None
            if fips:
                t["_fips"] = t[fips].astype(str).str.extract(r"(\d{4,5})")[0].str.zfill(5)
                est_row = t.apply(
                    lambda r: r.astype(str).str.contains("establish", case=False,
                                                         na=False).any(),
                    axis=1,
                )
                est_by_fips = (
                    pd.DataFrame({"_fips": t["_fips"], "est": est_row})
                    .dropna(subset=["_fips"]).groupby("_fips")["est"].any()
                )
                df["raw_tick_established"] = (
                    df["county_fips"].map(est_by_fips).fillna(False)
                    .map({True: "Y", False: "N"})
                )
        except Exception as exc:
            print(f"[lyme] could not parse tick file ({exc}); leaving established null")

    return df
