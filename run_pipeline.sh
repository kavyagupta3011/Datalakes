#!/usr/bin/env bash
# End-to-end orchestration for the generic Medallion lakehouse pipeline.
#
# NOTE: this is a development/demo convenience script. It is destructive -
# it clears bronze/ and silver/ and regenerates everything from scratch so
# you always get a clean, reproducible run. Do not point it at real data
# you care about.
set -euo pipefail
cd "$(dirname "$0")"

echo ">>> [1/7] Resetting bronze/ and silver/ ..."
rm -rf bronze silver catalog/catalog.json lineage/lineage.json mappings/column_mappings.json
mkdir -p bronze silver catalog lineage mappings

echo ">>> [2/7] Generating synthetic Bronze data (retail / education / support) ..."
python3 src/generate_bronze.py

echo ">>> [3/7] Building the lightweight file catalog ..."
python3 src/build_catalog.py

echo ">>> [4/7] Running Bronze -> Silver (decode, clean, harmonize, lineage) ..."
python3 src/bronze_to_silver.py

echo ">>> [5/7] Running Silver -> Gold (generic star schema into Postgres) ..."
python3 src/silver_to_gold.py

echo ">>> [6/7] Materializing OLAP cuboids ..."
python3 src/gold_olap.py

echo ">>> [7/7] Validating Gold + OLAP layer ..."
python3 src/validate_olap.py

echo ">>> Pipeline complete."
