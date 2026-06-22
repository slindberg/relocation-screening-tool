#!/usr/bin/env python3
"""
Connectivity / format probe. Hits each external endpoint once and reports what
comes back (status, content-type, size, whether it is a zip, first bytes). Run
this and share the output so any endpoint mismatch can be fixed precisely.

    python3 diagnose.py
"""
import requests

UA = {"User-Agent": "relocation-screening/1.0"}
PRISM = "https://data.prism.oregonstate.edu/normals/us/4km"


def probe(label, url, params=None):
    print(f"\n### {label}\n  {url}")
    if params:
        print(f"  params={params}")
    try:
        r = requests.get(url, params=params, headers=UA, timeout=60,
                         allow_redirects=True)
        ct = r.headers.get("Content-Type", "?")
        is_zip = r.content[:2] == b"PK"
        print(f"  HTTP {r.status_code} | type={ct} | bytes={len(r.content)} | "
              f"zip={is_zip} | final_url={r.url}")
        head = r.content[:240]
        try:
            print(f"  first: {head.decode('utf-8', 'replace')!r}")
        except Exception:
            print(f"  first(bytes): {head!r}")
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    # Census decennial population (key-free)
    probe("Census 2020 decennial population (place)",
          "https://api.census.gov/data/2020/dec/pl",
          {"get": "NAME,P1_001N", "for": "place:*"})

    # PRISM v2 normals — daily, monthly, annual for tmax/tdmean at 4km
    probe("PRISM normals DAILY tmax 0101 (4km)",
          f"{PRISM}/tmax/daily/prism_tmax_us_25m_20200101_avg_30y.zip")
    probe("PRISM normals MONTHLY tmax 01 (4km)",
          f"{PRISM}/tmax/monthly/prism_tmax_us_25m_202001_avg_30y.zip")
    probe("PRISM normals ANNUAL tdmean (4km)",
          f"{PRISM}/tdmean/monthly/prism_tdmean_us_25m_2020_avg_30y.zip")
    print("\nDone. Share everything above.")
