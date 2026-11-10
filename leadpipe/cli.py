"""Command line: `leadpipe run --city "Austin, TX"`"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from leadpipe.pipeline import RunOptions, run

DEFAULT_NICHE = Path(__file__).resolve().parent / "niches" / "dentists.toml"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="leadpipe", description="Find, enrich and score B2B leads.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the full pipeline for one city")
    r.add_argument("--city", required=True, help='e.g. "Austin, Texas"')
    r.add_argument("--niche", type=Path, default=DEFAULT_NICHE, help="niche TOML file (default: dentists)")
    r.add_argument("--limit", type=int, help="only process the first N leads (good for a quick test)")
    r.add_argument("--out", type=Path, default=Path("output"), help="where to write the CSV and report")
    r.add_argument("--db", type=Path, default=Path("data/leads.db"))
    r.add_argument("--refresh-days", type=float, default=14, help="re-crawl websites older than this")
    r.add_argument("--no-ai", action="store_true", help="rule-based score only")
    r.add_argument("--no-slack", action="store_true")
    r.add_argument("--source", choices=["auto", "google", "osm"], default="auto",
                   help="auto = Google Places if GOOGLE_MAPS_API_KEY is set, else OpenStreetMap")
    r.add_argument("--osm-file", type=Path, help="replay a saved Overpass JSON response (offline)")
    r.add_argument("--places-file", type=Path, help="save Google Places response here on first run, load it on subsequent runs (saves API quota)")
    r.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    result = run(RunOptions(
        city=args.city, niche_path=args.niche, out_dir=args.out, db_path=args.db, limit=args.limit,
        refresh_days=args.refresh_days, use_ai=not args.no_ai, send_slack=not args.no_slack,
        osm_file=args.osm_file, places_file=args.places_file, source=args.source,
    ))
    s = result.stats
    print(f"\n{s['found']} leads · {s['with_website']} with website · {s['with_email']} with email · "
          f"{s['no_booking']} without online booking · {s['hot']} hot · {s['new']} new")
    print(f"CSV:    {result.csv}\nReport: {result.report}")


if __name__ == "__main__":
    main()
