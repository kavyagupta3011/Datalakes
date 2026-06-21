# Generic Medallion Lakehouse — Interview Explainer

A study doc, not a README. Written so you can explain *what it is*, *why it's built that
way*, and *how each piece actually works* without reading code in the room.

---

## 1. The one-sentence pitch

> "I built a full-stack data lakehouse: a Python ETL pipeline that ingests messy files
> from multiple business domains, cleans and harmonizes them generically (no
> per-domain code), loads them into a real star schema in Postgres, pre-aggregates OLAP
> cubes on top, and exposes all of it through a FastAPI backend and a React dashboard
> where you can upload files, trigger pipeline runs, browse the tables, and visualize
> the cubes with drill-through back to the original source file."

That's the 20-second version. Everything below is the 20-minute version.

## 2. Why "Medallion architecture" and why it matters

Medallion architecture is a common data-lakehouse pattern with three layers:

- **Bronze** — raw data, exactly as it arrived. Nothing is cleaned or thrown away.
  This is your audit trail / replay source.
- **Silver** — cleaned, validated, schema-harmonized data. Still close to the
  source grain (one row per source record), but now typed and structurally consistent.
- **Gold** — business-ready, modeled data. In this project that means a proper
  **star schema** (dimension tables + fact tables) in Postgres, plus pre-aggregated
  **OLAP cubes** on top of the facts.

The reason this pattern is used in real companies: each layer has a different job and
different failure tolerance. Bronze must never be lossy. Silver is where you can safely
retry/reprocess because nothing destructive has happened yet. Gold is what
analysts/BI tools actually query, so it should be fast and modeled, not raw.

## 3. The core design decision: *generic*, not domain-specific

Most portfolio ETL projects hardcode column names for one dataset ("orders has
`order_id`, `customer_id`, `total`..."). This project deliberately doesn't. The same
code ingests **retail, education, and support** data side by side, and would ingest a
fourth domain you'd never seen, with zero code changes. That's the headline feature —
it proves the pipeline is an *engine*, not a script.

This is achieved through a few generic mechanisms (detailed in section 5):
- File-location convention (`bronze/<domain>/<entity>_<date>.<ext>`) instead of a
  manifest you'd have to maintain.
- A column-harmonization engine that resolves raw column names to canonical ones via a
  layered fallback (memory → alias config → heuristics → safe fallback), not a
  hardcoded mapping per dataset.
- A fact-vs-dimension classifier that uses structural signals (how many ID columns,
  numeric columns, date columns) instead of a hardcoded entity list.

## 4. The five pipeline stages, in order

```
generate_bronze.py  ->  build_catalog.py  ->  bronze_to_silver.py  ->  silver_to_gold.py  ->  gold_olap.py  ->  validate_olap.py
   (synthetic data)      (file registry)        (clean + harmonize)     (star schema)        (cubes)           (sanity check)
```

`orchestrator.py` runs the middle five as one command with retry/backoff per step.

### 4.1 `generate_bronze.py` — synthetic data generator
Produces realistic, intentionally *messy* test data for three domains (retail orders,
education grades, support tickets — plus customer/student/agent reference data) using
Faker: inconsistent column names, missing values, duplicate rows, mixed date formats.
This exists purely so the project is runnable end-to-end without needing real company
data — but the messiness is the point, it's what proves the cleaning logic works.

### 4.2 `build_catalog.py` — the file registry
Walks `bronze/`, and for every file:
- Infers `domain` and `entity` from its path/filename convention
  (`bronze/retail/orders_20260601.csv` → domain=`retail`, entity=`orders`). Files that
  don't follow the convention get `domain="unknown"` instead of crashing — the pipeline
  is designed to degrade gracefully, not fail, on messy input.
- Computes a SHA-256 checksum (fingerprint) of the file.
- Flags exact-duplicate files (same checksum, different filename).
- Writes everything to `catalog/catalog.json` — a lightweight, file-based registry.
  **Deliberately not** a database-backed governance catalog with RBAC/permissions —
  that's out of scope for what this project is trying to demonstrate.

**Interview talking point:** this is the "catalog" layer real lakehouses have (think
AWS Glue Catalog, Unity Catalog) — scaled down to what's needed for a single-engineer
demo: discovery + fingerprinting + dedup, not a full metastore.

### 4.3 `bronze_to_silver.py` — the actual ETL
The biggest, most important file. Per file:
1. **Decode**: format-specific decoders for CSV, Excel (multi-sheet, skips empty
   sheets), JSON (flat or nested via `json_normalize`), XML (tries pandas' built-in
   parser, falls back to manual ElementTree flattening for irregular XML).
2. **Structural clean**: normalize column names (lowercase, snake_case), strip
   whitespace from string cells, drop fully-empty rows, drop exact duplicate rows.
3. **Type inference** (`infer_and_cast`): for every column, decide boolean vs numeric
   vs datetime vs string using confidence thresholds against the *actual values*, not
   per-column hardcoding. E.g., a column is cast to boolean only if ≥90% of its values
   match a configured true/false vocabulary; numeric is checked *before* datetime
   specifically because pandas will happily (mis)parse plain numbers as datetime epoch
   offsets otherwise — a real bug this design avoids.
4. **Column harmonization** (delegates to `schema_engine.py` — see section 5): renames
   raw columns to canonical names.
5. **Lineage columns** are stamped onto every row: `_bronze_path`, `_domain`,
   `_entity`, `_format`, `_file_checksum`, `_processed_at`, `_pipeline_run_id`, and a
   `_row_checksum` (hash of the business columns, used later for incremental dedup).
   This is the traceability backbone of the whole project — **any row, anywhere
   downstream, can be traced back to the exact Bronze file and byte-checksum it came
   from.**
6. **Quality profile**: a `.profile.json` sidecar per file — row/column counts, null %
   per column, a completeness score, sample values, and schema-drift detection
   (compares this run's columns against the previous run's profile).
7. **Output**: written as **partitioned Parquet**
   (`silver/domain=.../entity=.../year=.../month=...`, Snappy-compressed) — the
   standard columnar format for analytics, with idempotent re-runs (skips files whose
   Silver output already exists) and multithreaded processing (`ThreadPoolExecutor`).
8. **Unstructured files** (images, audio, PDFs) are copied through as-is into the same
   partition scheme rather than forced through a tabular pipeline. Images and PDFs
   additionally get **offline OCR** via Tesseract (`pytesseract`) — no LLM, no API key
   — producing a `.ocr.json` sidecar with extracted text. OCR failures are caught and
   logged, never crash the run.

**Interview talking point:** type inference order (numeric-before-datetime) and the
idempotency-via-checksum design are good "tell me about a tricky bug/decision" answers.

### 4.4 `schema_engine.py` — the column-harmonization engine
This is what makes the pipeline genuinely domain-agnostic instead of just "handles
three datasets I happened to test." For every raw column name, it tries, **in order**:

1. **Mapping memory** (`mappings/column_mappings.json`) — has this exact
   domain+entity+column been seen and *approved* before? If so, reuse it.
2. **Alias config** (`config/domain_aliases.yaml`) — exact match against a configured
   list of known aliases for a canonical column (e.g. `cust_id`, `customer_no` →
   `customer_id`). Curated by a human, so it's auto-approved straight into memory.
3. **Heuristic name-contains rules** (`config/heuristics.yaml`) — substring rules,
   e.g. anything containing `"email"` maps to `email`.
4. **Heuristic numeric-range guessing** — only applies to genuinely unhelpful names
   like `unnamed_0`/`col_3`; guesses based on the *value distribution* (e.g. values
   consistently between 1–5 might be a rating).
5. **Fallback** — just normalize the raw name and flag it for review.

There's no numeric confidence score anywhere in this — first matching rule wins.
Trust is just "which method resolved this column": alias-config and already-approved
memory hits are auto-approved; heuristic and fallback hits are still applied
immediately (the pipeline never blocks waiting on a human) but get tagged
`needs_review` so they're visible. Every decision is written into mapping memory, so
**the system gets smarter over time** — the next file with the same shape resolves
instantly from memory instead of re-running heuristics.

**Interview talking point:** this is a deliberately simple stand-in for what
data-catalog / schema-registry tools do in production — "resolve semantic meaning of
a column without a human writing a mapping for every single source." The
method-based trust + review-flagging pattern (rather than silently guessing, and
rather than inventing an arbitrary numeric confidence score) is the important bit to
mention — it's an explicit human-in-the-loop design for the cases the heuristics get
wrong, without pretending the system can quantify how sure it is.

### 4.5 `silver_to_gold.py` — building the star schema (Postgres)
Reads every partitioned Parquet file back into Pandas, grouped by (domain, entity),
then:

1. **Classifies each entity as dimension or fact** (`classify_entity`), structurally:
   - **fact** if it has ≥2 ID-like columns (references other entities), OR has any
     numeric measure column, OR has ≥2 date columns (suggests an event with a
     duration, e.g. `opened_at`/`closed_at`).
   - **dimension** otherwise (looks like master/reference data).
   - This can be **overridden** per `domain.entity` in `config/entity_overrides.yaml`
     if the heuristic gets a specific case wrong — overrides are checked first and
     logged when applied. (Documented as an honest "this heuristic won't be perfect on
     every schema" tradeoff, not pretending it's flawless.)
2. **Builds `dim_date`** — a generic calendar dimension auto-derived from every
   date column found across every entity (year/quarter/month/day/day-of-week/weekend
   flag), keyed by `YYYYMMDD` integer.
3. **Builds `dim_<entity>`** tables for everything classified as a dimension — picks
   the entity's natural ID column, dedupes, assigns a surrogate key.
4. **Builds `fact_<domain>_<entity>`** tables for everything classified as a fact —
   resolves foreign keys automatically by matching each fact's reference columns
   against dimension natural IDs, and links date columns to `dim_date` via the
   `YYYYMMDD` key. Lineage columns survive into Gold untouched.
5. **Incremental loading by default**: dimensions append only natural-key rows not
   already present (surrogate keys continue from `MAX(key)+1`); facts append only rows
   whose `_row_checksum` isn't already present (`fact_id` continues from
   `MAX(fact_id)+1`). Re-running the pipeline never duplicates data. `--full-reload`
   truncates and rebuilds everything from scratch (e.g. after a schema change).

**Interview talking point:** this is a real, from-scratch implementation of
incremental/CDC-style loading using a content checksum as the dedup key instead of a
source-provided "last modified" timestamp — useful if asked "how do you avoid
double-counting on re-runs?"

### 4.6 `gold_olap.py` — OLAP cuboid materializer
"OLAP cube" = pre-aggregated rollups so dashboards don't have to recompute sums over
millions of fact rows on every page load. For **every** fact table found (again, no
hardcoded list), it materializes:

- `cuboid_<fact>_apex` — one row per numeric measure: grand total `sum`/`count`/`mean`.
- `cuboid_<fact>_by_month` — rollup by year/month (if the fact has a date key).
- `cuboid_<fact>_by_<dim_fk>` — one slice per dimension foreign key present
  (e.g. `cuboid_fact_retail_orders_by_customers_key`).
- `cuboid_<fact>_by_month_<dim_fk>` — the "dice": month × dimension combo.

If a fact has no numeric measure at all (e.g. a pure event log), it falls back to
counting rows (`_record_count`) so cubes still make sense.

It also implements `drill_to_source(fact_table, **filters)` — given a cuboid grouping
key (say `customers_key=3, year=2026, month=6`), it returns the underlying fact rows,
which still carry `_bronze_path`/`_file_checksum`. That's the full chain:
**cube number → fact rows → Silver partition → exact Bronze file.** This drill-through
is exposed in the frontend's cuboid viewer (click a bar/point → see the source rows).

### 4.7 `validate_olap.py` — automated sanity check
Re-derives the apex cuboid's sum/count *independently* straight from the fact table and
fails loudly if it doesn't match what `gold_olap.py` materialized — catches aggregation
bugs automatically rather than trusting the cube blindly. Also checks every fact table
has a cuboid and no cuboid is empty.

### 4.8 `orchestrator.py` — ties it together
Runs `build_catalog → bronze_to_silver → silver_to_gold → gold_olap → validate_olap` as
subprocesses, with **per-step retry and exponential backoff** on failure, persisting
progress to `catalog/orchestration_state.json` after every step. `--resume` skips steps
that already succeeded so a crashed run doesn't restart from scratch. Deliberately a
single dependency-free script rather than pulling in Airflow/Dagster — a documented,
intentional scope tradeoff for a demo project, not an oversight.

## 5. The API layer (`src/api.py`, FastAPI)

Originally fully read-only; now has a small, deliberate write surface to support the
frontend:

| Endpoint | Purpose |
|---|---|
| `GET /health` | DB connectivity check |
| `GET /tables` | lists every `dim_/fact_/cuboid_` table, grouped |
| `GET /tables/{name}?limit=&offset=` | paginated rows from any table |
| `GET /drill/{fact_table}?<keys>` | underlying fact rows for a cuboid slice (calls `drill_to_source`) |
| `POST /upload` | multipart file upload straight into `bronze/<domain>/<entity>_<timestamp>.<ext>`, with extension validation and name sanitization |
| `POST /pipeline/run?full_reload=` | kicks off the orchestrator as a background subprocess (returns 409 if one's already running) |
| `GET /pipeline/status` | live status (per-step success/fail, attempts) for the frontend to poll |

No auth — explicitly a portfolio-demo decision, called out in the README rather than
glossed over. CORS is open so the React dev server / Docker frontend can call it
cross-origin.

**Interview talking point:** be ready to explain *why* you added a write surface at
all — "the API started read-only by design, but a frontend that can only *display*
isn't interactive, so I added the smallest possible write surface (upload + trigger)
needed to make the demo end-to-end usable, while keeping it unauthenticated and
clearly scoped since this isn't a production system."

## 6. The frontend (`frontend/`, React + Vite + TypeScript + Tailwind)

Four views, talking to the API via a small typed `api.ts` client:

- **Dashboard** — live counts (dimension/fact/cuboid tables), last pipeline run status
  per step, polls every 8s.
- **Upload** — drag-and-drop (or click-to-browse) file upload into Bronze with a
  domain/entity picker, plus a panel to trigger the pipeline (incremental or full
  reload) and watch live status while it runs.
- **Table Explorer** — browse every dimension/fact/cuboid table with pagination,
  lineage columns visually highlighted.
- **Cuboid Viewer** — pick a fact table, see its apex totals as stat cards, a monthly
  trend line chart, and bar charts per dimension (with dimension foreign keys resolved
  to human-readable labels by cross-referencing the matching `dim_<entity>` table).
  **Click a bar or chart point to drill through** to the actual underlying fact rows
  via `/drill/{fact_table}` — ties the whole UI back to the lineage story.

Styled with a custom Tailwind theme (lavender/sky-blue palette, gradient accents).
Charts via Recharts, icons via lucide-react.

**Interview talking point:** the drill-through interaction is the strongest "this
isn't just CRUD" feature to demo live — clicking a chart bar and watching it resolve
all the way back to source rows demonstrates the lineage design end-to-end, visually.

## 7. Testing, Docker, CI

- **pytest suite** (22 tests): unit tests for type inference and entity classification
  (no DB needed), integration tests for incremental dimension/fact loads against a
  real Postgres (auto-skipped, not failed, if no DB is reachable).
- **Docker**: a single app image (Python 3.11 + Tesseract) reused for both the
  one-shot `pipeline` job and the long-running `api` service; a separate multi-stage
  `frontend` image (Node build → nginx). `docker-compose.yml` wires up Postgres → the
  one-shot pipeline → the API → the frontend, with `api` and `pipeline` sharing the
  same Bronze/Silver named volumes so uploads through the UI are visible to
  pipeline runs triggered through the UI.
- **GitHub Actions CI**: spins up a Postgres service container and runs the full test
  suite on every push/PR.

## 8. Likely interview questions and how to answer them

**"Why generic instead of just building it for one dataset?"**
Because the interesting engineering problem in a real lakehouse isn't "can you write
an ETL for orders.csv" — it's "can your pipeline survive a new data source showing up
with different column names and no warning." Building it generic from day one forced
real design decisions (the harmonization engine, the classifier, lineage-by-default)
instead of hardcoding around them.

**"How do you avoid reprocessing/duplicating data on every run?"**
Bronze→Silver is idempotent via "does the Silver output already exist." Silver→Gold
is incremental via checksums: dimensions dedupe on natural ID, facts dedupe on a
`_row_checksum` hash of the business columns, both with surrogate keys continuing from
the current max rather than being reassigned.

**"What happens when the column-mapping heuristics get it wrong?"**
Heuristic/fallback mappings are still applied (so the pipeline never blocks), but flagged
in `mappings/column_mappings.json` as unapproved — visible for a human to review and
correct, and `config/entity_overrides.yaml` / `config/domain_aliases.yaml` exist as the
explicit override mechanisms once you know what's wrong.

**"Why Postgres and not a real lakehouse table format (Delta/Iceberg)?"**
Scope/time tradeoff for a single-engineer demo — Postgres gives you a real SQL star
schema with FK semantics and ACID writes without needing a Spark cluster. The
Bronze/Silver layers (Parquet on disk, partitioned by domain/entity/year/month) are
already in the columnar format real lakehouses use; swapping the Gold sink for
Delta/Iceberg on object storage would be the natural next step.

**"What would you change/add with more time?"**
Auth on the write endpoints, a proper metadata/governance layer instead of the
file-based catalog, code-splitting the frontend bundle (it's a bit over the default
chunk-size warning), and a real DAG scheduler if step dependencies got more complex
than a linear chain.

---

*Tip for the interview: don't try to recite this. Pick 2–3 sections you find most
interesting (the harmonization engine and the drill-through UI are the most
demo-able) and be ready to go deep on those — that's more convincing than a shallow
tour of everything.*
