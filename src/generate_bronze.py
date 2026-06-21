"""
generate_bronze.py
-------------------
Creates synthetic raw data across three deliberately different business
domains (retail, education, support) to prove the pipeline is genuinely
generic and not hardcoded to one of them. Every file has real-world data
problems baked in on purpose (dupes, nulls, whitespace, mixed date formats,
ugly form-style headers, multi-sheet Excel with an empty sheet, etc.) so the
Silver layer's cleaning logic has something real to do.

Run: python src/generate_bronze.py
"""
import random
from datetime import datetime, timedelta

import pandas as pd
from faker import Faker

from common import BRONZE_DIR, log

fake = Faker()
Faker.seed(42)
random.seed(42)

N_CUSTOMERS = 120
N_ORDERS = 300
N_FEEDBACK = 40
N_STUDENTS = 100
N_GRADES = 250
N_TICKETS = 150
N_AGENTS = 15


def _mkdir(domain: str):
    p = BRONZE_DIR / domain
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# RETAIL DOMAIN
# ---------------------------------------------------------------------------
def gen_retail_customers(folder):
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        rows.append(
            {
                "Customer Id": f"CUST{i:04d}",
                "  Full Name ": f" {fake.name()} " if i % 7 == 0 else fake.name(),
                "Email Address": fake.email(),
                "Sign Up Date": fake.date_between(start_date="-3y", end_date="today").strftime(
                    "%Y-%m-%d" if i % 3 else "%d/%m/%Y"
                ),
                "Country": fake.country(),
            }
        )
    # inject a duplicate row (classic dedup test)
    rows.append(rows[5].copy())
    df = pd.DataFrame(rows)
    out = folder / "customers_20260601.csv"
    df.to_csv(out, index=False)
    log(f"wrote {out} ({len(df)} rows)")


def gen_retail_orders(folder):
    rows = []
    for i in range(1, N_ORDERS + 1):
        amount = round(random.uniform(5, 500), 2)
        rows.append(
            {
                "Order No": f"ORD{i:05d}",
                "Cust Id": f"CUST{random.randint(1, N_CUSTOMERS):04d}",
                "Order Dt": fake.date_between(start_date="-1y", end_date="today").strftime(
                    "%Y-%m-%d" if i % 2 else "%m/%d/%Y"
                ),
                "Order Total": amount if random.random() > 0.05 else "N/A",
                "Order Status": random.choice(["completed", "PENDING", "Cancelled", "completed"]),
            }
        )
    df = pd.DataFrame(rows)
    out = folder / "orders_20260603.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Orders", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="Notes", index=False)  # intentionally empty sheet
    log(f"wrote {out} ({len(df)} rows, 1 empty sheet)")


def gen_retail_feedback(folder):
    """Simulates a Google-Form-style export: ugly question-style headers."""
    rows = []
    for i in range(1, N_FEEDBACK + 1):
        rows.append(
            {
                "Timestamp": datetime.now().isoformat(),
                "What is your Customer ID?": f"CUST{random.randint(1, N_CUSTOMERS):04d}",
                "How satisfied are you (1-5)?": random.choice([1, 2, 3, 4, 5, None]),
                "Any additional comments?": random.choice(
                    ["Great service!", "Could be faster", "", None, "Loved it"]
                ),
            }
        )
    df = pd.DataFrame(rows)
    out = folder / "feedback_20260605.csv"
    df.to_csv(out, index=False)
    log(f"wrote {out} ({len(df)} rows, form-style headers)")


def gen_retail_mystery(folder_root):
    """A file with no domain/entity folder convention at all - tests fallback."""
    rows = [{"a": i, "b": fake.word(), "c": random.random()} for i in range(20)]
    out = folder_root / "random_export_2026.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    log(f"wrote {out} (no domain/entity convention - tests fallback handling)")


# ---------------------------------------------------------------------------
# EDUCATION DOMAIN
# ---------------------------------------------------------------------------
def gen_education_students(folder):
    import json

    records = []
    for i in range(1, N_STUDENTS + 1):
        records.append(
            {
                "roll_no": f"S{i:04d}",
                "name": fake.name(),
                "date of birth": fake.date_of_birth(minimum_age=18, maximum_age=24).isoformat(),
                "dept": random.choice(["CS", "ECE", "ME", "CE"]),
                "email address": fake.email(),
            }
        )
    out = folder / "students_20260602.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    log(f"wrote {out} ({len(records)} records)")


def gen_education_grades(folder):
    """Moodle-style nested XML export."""
    lines = ["<grades>"]
    courses = ["CS101", "CS202", "MA110", "PH150"]
    for i in range(1, N_GRADES + 1):
        grade = random.choice(["A", "B", "C", "D", "F"])
        points = {"A": 10, "B": 8, "C": 6, "D": 4, "F": 0}[grade]
        lines.append(
            "  <record>"
            f"<roll_no>S{random.randint(1, N_STUDENTS):04d}</roll_no>"
            f"<course_code>{random.choice(courses)}</course_code>"
            f"<final_grade>{grade}</final_grade>"
            f"<points>{points}</points>"
            f"<credit_hours>{random.choice([3, 4])}</credit_hours>"
            "</record>"
        )
    lines.append("</grades>")
    out = folder / "grades_20260604.xml"
    out.write_text("\n".join(lines), encoding="utf-8")
    log(f"wrote {out} ({N_GRADES} records)")


# ---------------------------------------------------------------------------
# SUPPORT DOMAIN
# ---------------------------------------------------------------------------
def gen_support_tickets(folder):
    rows = []
    date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"]
    for i in range(1, N_TICKETS + 1):
        created = fake.date_time_between(start_date="-180d", end_date="now")
        is_resolved = random.random() > 0.3
        row = {
            "Case ID": f"TKT{i:05d}",
            " Contact Email ": fake.email(),
            "Title": random.choice(
                ["Login issue", "Billing question", "Feature request", "Bug report", ""]
            ),
            "Severity": random.choice(["low", "medium", "HIGH", "critical", None]),
            "Opened At": created.strftime(random.choice(date_formats)),
            "Closed At": (
                (created + timedelta(days=random.randint(0, 10))).strftime(
                    random.choice(date_formats)
                )
                if is_resolved
                else None
            ),
            "Ticket Status": "resolved" if is_resolved else random.choice(["open", "OPEN", "in_progress"]),
        }
        rows.append(row)
    # blank row + a fully duplicate row to test cleaning
    rows.append({k: None for k in rows[0]})
    rows.append(rows[3].copy())
    df = pd.DataFrame(rows)
    out = folder / "tickets_20260607.csv"
    df.to_csv(out, index=False)
    log(f"wrote {out} ({len(df)} rows, dirty)")


def gen_support_agents(folder):
    import json

    records = []
    for i in range(1, N_AGENTS + 1):
        records.append(
            {
                "agent no": f"AGT{i:03d}",
                "name": fake.name(),
                "department": random.choice(["Tier1", "Tier2", "Billing"]),
                "start_date": fake.date_between(start_date="-5y", end_date="-30d").isoformat(),
            }
        )
    out = folder / "agents_20260601.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    log(f"wrote {out} ({len(records)} records)")


def gen_support_attachment(folder):
    """
    A real PNG with rendered text, to exercise both the bronze->silver
    unstructured passthrough path AND the OCR text-extraction step.
    """
    out = folder / "ticket_attachment_20260608.png"
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (500, 160), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), "SUPPORT TICKET TKT00042", fill="black")
        draw.text((10, 40), "Customer reports login failure", fill="black")
        draw.text((10, 70), "after password reset on mobile app.", fill="black")
        draw.text((10, 110), "Severity: HIGH", fill="black")
        img.save(out)
        log(f"wrote {out} (real PNG with rendered text, for OCR testing)")
    except ImportError:
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # fallback: not a real PNG, just bytes
        log(f"wrote {out} (Pillow unavailable - wrote dummy passthrough bytes instead)")


def main():
    log("Generating synthetic Bronze data across retail / education / support domains...")
    retail = _mkdir("retail")
    education = _mkdir("education")
    support = _mkdir("support")

    gen_retail_customers(retail)
    gen_retail_orders(retail)
    gen_retail_feedback(retail)
    gen_education_students(education)
    gen_education_grades(education)
    gen_support_tickets(support)
    gen_support_agents(support)
    gen_support_attachment(support)
    gen_retail_mystery(BRONZE_DIR)  # dropped at bronze/ root, no domain folder

    log("Bronze generation complete.")


if __name__ == "__main__":
    main()
