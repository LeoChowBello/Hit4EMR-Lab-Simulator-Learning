#!/usr/bin/env python3
"""
Hit4EMR lab simulator.

This script prepares a student-friendly OpenEMR environment, seeds the sample
lab catalog, and watches the outgoing EDI folder so results can come back into
OpenEMR automatically.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pymysql
except ImportError:  # pragma: no cover
    pymysql = None

LAB_NAME = "Ontario Reference Lab"
CATALOG = {
    "6690-2": dict(name="WBC", unit="x10^9/L", low=4.0, high=11.0),
    "718-7": dict(name="Hemoglobin", unit="g/L", low=120.0, high=175.0),
    "1558-6": dict(name="Glucose (Fasting)", unit="mmol/L", low=3.6, high=6.0),
    "3016-3": dict(name="TSH", unit="mIU/L", low=0.32, high=4.0),
    "2093-3": dict(name="Total Cholesterol", unit="mmol/L", low=0.0, high=5.2),
    "4548-4": dict(name="Hemoglobin A1c", unit="%", low=4.0, high=6.0),
}
DIAGNOSIS_CATALOG = [
    ("R79.89", "Other specified abnormal findings of blood chemistry"),
    ("D64.9", "Anemia, unspecified"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("E78.5", "Hyperlipidemia, unspecified"),
    ("E03.9", "Hypothyroidism, unspecified"),
    ("R73.03", "Prediabetes"),
    ("R53.83", "Other fatigue"),
    ("N18.9", "Chronic kidney disease, unspecified"),
    ("Z13.1", "Encounter for screening for diabetes mellitus"),
    ("Z13.220", "Encounter for screening for lipoid disorders"),
]
PROBLEM_DIAGNOSIS_RULES = [
    (("microalbuminuria due to type 2 diabetes mellitus", "proteinuria due to type 2 diabetes mellitus"), "E11.29"),
    (("disorder of kidney due to diabetes mellitus",), "E11.21"),
    (("diabetes mellitus type 2", "type 2 diabetes", "later type 2 diabetes"), "E11.9"),
    (("prediabetes",), "R73.03"),
    (("essential hypertension", "hypertension"), "I10"),
    (("hypertension risk",), "Z91.89"),
    (("dyslipidemia", "hypertriglyceridemia", "hyperlipidemia"), "E78.5"),
    (("ckd stage 3",), "N18.30"),
    (("ckd",), "N18.9"),
    (("cad", "ischemic heart disease"), "I25.10"),
    (("mi history", "acute non-st segment elevation myocardial infarction"), "I25.2"),
    (("asthma",), "J45.909"),
    (("copd",), "J44.9"),
    (("depression",), "F32.A"),
    (("allergic rhinitis",), "J30.9"),
    (("chronic sinusitis",), "J32.9"),
    (("migraine",), "G43.909"),
    (("obesity",), "E66.9"),
    (("anemia of chronic disease", "anemia (disorder)"), "D64.9"),
    (("falls",), "R29.6"),
    (("loss of teeth",), "K08.109"),
    (("metabolic syndrome x",), "E88.81"),
    (("otitis media",), "H66.90"),
    (("osteoporosis risk",), "Z91.89"),
    (("smoking history",), "Z87.891"),
    (("sports clearance",), "Z02.5"),
]
COMMON_ROOTS = [
    Path("/var/www/localhost/htdocs/openemr"),
    Path("/var/www/html/openemr"),
    Path("/var/www/openemr"),
    Path("/opt/openemr"),
    Path("/srv/openemr"),
]
COMPOSE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose-8.0.x.yml",
)


def log(message):
    print(message, flush=True)


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def write_text(path, content):
    Path(path).write_text(content, encoding="utf-8")


def discover_layout():
    raw_mode = os.getenv("ONTARIO_LAB_MODE", "").strip().lower()
    sqlconf_env = os.getenv("OPENEMR_SQLCONF", "").strip()
    sites_env = os.getenv("OPENEMR_SITES", "").strip()
    root_env = os.getenv("OPENEMR_ROOT", "").strip()
    compose_file = find_compose_file()

    if sqlconf_env:
        sqlconf = Path(sqlconf_env).expanduser().resolve()
        if sqlconf.exists():
            return derive_layout(raw_mode or "host", sqlconf.parent.parent.parent, sqlconf.parent.parent, sqlconf, compose_file)

    if sites_env:
        sites = Path(sites_env).expanduser().resolve()
        sqlconf = sites / "default" / "sqlconf.php"
        if sqlconf.exists():
            return derive_layout(raw_mode or "host", sites.parent, sites, sqlconf, compose_file)

    if root_env:
        root = Path(root_env).expanduser().resolve()
        sqlconf = root / "sites" / "default" / "sqlconf.php"
        if sqlconf.exists():
            return derive_layout(raw_mode or "host", root, root / "sites", sqlconf, compose_file)

    for root in COMMON_ROOTS:
        sqlconf = root / "sites" / "default" / "sqlconf.php"
        if sqlconf.exists():
            return derive_layout(raw_mode or "host", root, root / "sites", sqlconf, compose_file)

    if raw_mode == "docker" and compose_file:
        return parse_compose_for_sites(compose_file)

    raise FileNotFoundError(
        "Could not find an OpenEMR install. Set OPENEMR_ROOT, OPENEMR_SITES, or OPENEMR_SQLCONF."
    )


def find_compose_file():
    cwd = Path.cwd()
    for name in COMPOSE_NAMES:
        candidate = cwd / name
        if candidate.exists():
            return candidate
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml"):
        matches = sorted(cwd.glob(pattern))
        if matches:
            return matches[0]
    return None


def derive_layout(mode, root, sites, sqlconf, compose_file=None):
    root = Path(root).expanduser().resolve()
    sites = Path(sites).expanduser().resolve()
    sqlconf = Path(sqlconf).expanduser().resolve()
    return {
        "mode": mode,
        "compose_file": str(compose_file) if compose_file else "",
        "openemr_root": str(root),
        "sites_root": str(sites),
        "sqlconf_path": str(sqlconf),
        "edi_base": str(sites / "default" / "documents" / "edi"),
        "common_php": str(root / "interface" / "forms" / "procedure_order" / "common.php"),
    }


def parse_compose_for_sites(compose_file):
    text = Path(compose_file).read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        match = re.match(r"^\s*-\s*[^:]+:(/[^#\s]+)", line)
        if not match:
            continue
        mount_path = match.group(1).rstrip("/")
        if mount_path.endswith("/sites") or "/openemr/sites" in mount_path:
            return derive_layout("docker", Path(mount_path).parent.parent, Path(mount_path), Path(mount_path) / "default" / "sqlconf.php", compose_file)
    raise RuntimeError(
        f"Could not find an OpenEMR sites mount in {compose_file}. Expected a mount ending in /sites."
    )


def parse_sqlconf(sqlconf_path):
    text = read_text(sqlconf_path)

    def pick(name, default=""):
        pattern = rf"\${name}\s*=\s*['\"]([^'\"]*)['\"]"
        match = re.search(pattern, text)
        return match.group(1) if match else default

    return {
        "host": pick("host", "127.0.0.1"),
        "login": pick("login", ""),
        "pass": pick("pass", ""),
        "dbase": pick("dbase", ""),
        "socket": pick("socket", ""),
        "port": pick("port", "3306"),
    }


def connect_db(layout):
    if pymysql is None:
        raise RuntimeError("PyMySQL is required. Install it with: pip install pymysql")

    config = parse_sqlconf(layout["sqlconf_path"])
    params = {
        "user": config["login"],
        "password": config["pass"],
        "database": config["dbase"],
        "charset": "utf8mb4",
        "autocommit": False,
    }

    socket_path = config.get("socket", "")
    if socket_path and Path(socket_path).exists():
        params["unix_socket"] = socket_path
    else:
        params["host"] = config["host"] or "127.0.0.1"
        try:
            params["port"] = int(config.get("port") or 3306)
        except ValueError:
            params["port"] = 3306

    return pymysql.connect(**params)


def ensure_edi_paths(layout):
    base = Path(layout["edi_base"])
    for path in (base / "orders", base / "inbox"):
        path.mkdir(parents=True, exist_ok=True)


def seed_procedure_catalog(layout):
    log("Configuring OpenEMR procedure catalog...")
    conn = connect_db(layout)
    cur = conn.cursor()
    try:
        orders_path = f"{layout['edi_base']}/orders"
        results_path = f"{layout['edi_base']}/inbox"

        cur.execute(
            """
            INSERT IGNORE INTO procedure_providers
              (name, npi, active, direction, protocol, orders_path, results_path)
            VALUES (%s, %s, 1, %s, %s, %s, %s)
            """,
            (LAB_NAME, "123456", "B", "FS", orders_path, results_path),
        )

        cur.execute("SELECT ppid FROM procedure_providers WHERE name=%s LIMIT 1", (LAB_NAME,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Lab provider row was not created")
        lab_id = row[0]

        cur.execute("DELETE FROM procedure_type WHERE lab_id=%s", (lab_id,))
        cur.execute(
            """
            INSERT INTO procedure_type
              (parent, name, lab_id, procedure_code, procedure_type, activity)
            VALUES (0, %s, %s, %s, %s, 1)
            """,
            ("Ontario Labs", lab_id, "ONT-GRP", "fgp"),
        )
        parent_id = cur.lastrowid

        for code, data in CATALOG.items():
            cur.execute(
                """
                INSERT INTO procedure_type
                  (parent, name, lab_id, procedure_code, procedure_type, units, `range`, activity, procedure_type_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
                """,
                (
                    parent_id,
                    data["name"],
                    lab_id,
                    code,
                    "ord",
                    data["unit"],
                    f"{data['low']}-{data['high']}",
                    data["name"],
                ),
            )

        conn.commit()
        cur.execute("SELECT COUNT(*) FROM procedure_type WHERE lab_id=%s AND procedure_type='ord'", (lab_id,))
        test_count = cur.fetchone()[0]
        log(f"  ✓ Seeded {test_count} sample lab tests")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def seed_diagnosis_codes(layout):
    log("Seeding diagnosis codes...")
    conn = connect_db(layout)
    cur = conn.cursor()
    try:
        codes = [code for code, _ in DIAGNOSIS_CATALOG]
        if codes:
            placeholders = ",".join(["%s"] * len(codes))
            cur.execute(
                f"DELETE FROM icd10_dx_order_code WHERE dx_code IN ({placeholders}) OR formatted_dx_code IN ({placeholders})",
                codes + codes,
            )
            cur.executemany(
                """
                INSERT INTO icd10_dx_order_code
                  (dx_code, formatted_dx_code, valid_for_coding, short_desc, long_desc, active, revision)
                VALUES (%s, %s, '1', %s, %s, 1, 0)
                """,
                [(code, code, desc[:60], desc) for code, desc in DIAGNOSIS_CATALOG],
            )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM icd10_dx_order_code WHERE active=1 AND valid_for_coding='1'")
        log(f"  ✓ Diagnosis rows available: {cur.fetchone()[0]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def seed_problem_history(layout):
    log("Backfilling chart diagnosis history...")
    conn = connect_db(layout)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, title, diagnosis FROM lists WHERE type='medical_problem' AND activity=1 ORDER BY id"
        )
        rows = cur.fetchall()
        updated = 0
        for row_id, title, diagnosis in rows:
            if diagnosis:
                continue
            title_l = (title or "").strip().lower()
            code = "R79.89"
            for needles, mapped in PROBLEM_DIAGNOSIS_RULES:
                if any(needle in title_l for needle in needles):
                    code = mapped
                    break
            cur.execute("UPDATE lists SET diagnosis=%s WHERE id=%s", (f"ICD10:{code}", row_id))
            updated += 1
        conn.commit()
        log(f"  ✓ Problem history rows updated: {updated}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def patch_order_form(layout):
    path = Path(layout["common_php"])
    if not path.exists():
        log(f"Skipping order-form patch; file not found: {path}")
        return

    try:
        original = read_text(path)
        updated = original
        replacements = [
            (
                r"if\s*\(\s*\$_POST\[(?:\"|')form_provider_id(?:\"|')\]\s*\+\s*0\s*<\s*1\s*\)",
                'if (false && $_POST["form_provider_id"] + 0 < 1)',
            ),
            (r"if\s*\(\s*\$diag_flag\s*===\s*0\s*\)", "if (false && $diag_flag === 0)"),
            (
                r"if\s*\(\s*!\$_POST\[(?:\"|')form_date_collected(?:\"|')\]\s*&&\s*!\$_POST\[(?:\"|')form_order_psc(?:\"|')\]\s*\)",
                'if (false && !$_POST["form_date_collected"] && !$_POST["form_order_psc"])',
            ),
            (
                r"if\s*\(\s*empty\(\$_POST\[(?:\"|')form_billing_type(?:\"|')\]\)\s*\)",
                'if (false && empty($_POST["form_billing_type"]))',
            ),
        ]
        for pattern, replacement in replacements:
            updated = re.sub(pattern, replacement, updated)

        if updated == original:
            log("Order-form patch not needed on this OpenEMR version.")
            return

        backup = path.with_suffix(path.suffix + ".ontario-lab.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        write_text(path, updated)
        log(f"  ✓ Patched order-form validation: {path}")
    except PermissionError:
        log("  ! Could not patch the order form because of file permissions. Continuing.")
    except Exception as exc:
        log(f"  ! Order-form patch skipped: {exc}")


def parse_order_message(content):
    patient = {"fname": "Patient", "lname": "Unknown"}
    tests = []

    for line in content.splitlines():
        if line.startswith("PID|"):
            parts = line.split("|")
            if len(parts) > 5:
                name_bits = parts[5].split("^")
                if name_bits and name_bits[0]:
                    patient["lname"] = name_bits[0]
                if len(name_bits) > 1 and name_bits[1]:
                    patient["fname"] = name_bits[1]
        elif line.startswith("OBR|"):
            match = re.search(r"\|\|([^\|\^]+)\^", line)
            if match:
                tests.append(match.group(1))

    return patient, tests


def build_result_message(order_text):
    patient, tests = parse_order_message(order_text)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    control_id = f"ONT{timestamp}"

    lines = [
        f"MSH|^~\\&|ONTARIOLAB|LAB|OPENEMR|CLINIC|{timestamp}||ORU^R01|{control_id}|D|2.3",
        f"PID|1||1||{patient['lname']}^{patient['fname']}||19800101|M",
    ]

    result_rows = []
    for sequence, code in enumerate(tests, start=1):
        meta = CATALOG.get(code, {"name": "Test", "unit": "units", "low": 0.0, "high": 100.0})
        value = round(random.uniform(meta["low"], meta["high"]), 1)
        result_rows.append(
            {
                "sequence": sequence,
                "code": code,
                "name": meta["name"],
                "unit": meta["unit"],
                "value": value,
                "range": f"{meta['low']}-{meta['high']}",
                "abnormal": "N",
            }
        )
        lines.append(f"OBR|{sequence}|{control_id}||{code}^{meta['name']}^LN|||{timestamp}|||||||||||F")
        lines.append(
            f"OBX|1|NM|{code}^{meta['name']}^LN||{value}|{meta['unit']}|{meta['low']}-{meta['high']}|N|||F"
        )

    return "\n".join(lines) + "\n", result_rows, control_id


def find_latest_order_id(conn, patient_fname, patient_lname):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM patient_data WHERE fname=%s AND lname=%s LIMIT 1",
            (patient_fname, patient_lname),
        )
        patient = cur.fetchone()
        if not patient:
            return None

        patient_id = patient[0]
        cur.execute(
            "SELECT procedure_order_id FROM procedure_order WHERE patient_id=%s ORDER BY procedure_order_id DESC LIMIT 1",
            (patient_id,),
        )
        order_row = cur.fetchone()
        return order_row[0] if order_row else None
    finally:
        cur.close()


def publish_openemr_results(conn, order_id, control_id, result_rows):
    if not result_rows:
        return 0

    cur = conn.cursor()
    inserted = 0
    try:
        cur.execute(
            "SELECT procedure_order_id, control_id FROM procedure_order WHERE procedure_order_id=%s",
            (order_id,),
        )
        order_row = cur.fetchone()
        if not order_row:
            raise RuntimeError(f"procedure_order {order_id} was not found")

        if not order_row[1]:
            cur.execute(
                "UPDATE procedure_order SET control_id=%s WHERE procedure_order_id=%s",
                (control_id, order_id),
            )

        for item in result_rows:
            cur.execute(
                """
                SELECT ps.procedure_result_id
                FROM procedure_report pr
                JOIN procedure_result ps ON ps.procedure_report_id = pr.procedure_report_id
                WHERE pr.procedure_order_id = %s
                  AND pr.procedure_order_seq = %s
                  AND ps.result_code = %s
                LIMIT 1
                """,
                (order_id, item["sequence"], item["code"]),
            )
            if cur.fetchone():
                continue

            cur.execute(
                """
                INSERT INTO procedure_report
                  (procedure_order_id, procedure_order_seq, date_collected, date_report,
                   source, specimen_num, report_status, review_status, report_notes)
                VALUES (%s, %s, NOW(), NOW(), 0, '', 'final', 'received', '')
                """,
                (order_id, item["sequence"]),
            )
            report_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO procedure_result
                  (procedure_report_id, result_data_type, result_code, result_text, date,
                   facility, units, result, `range`, abnormal, comments, document_id,
                   result_status, date_end)
                VALUES (%s, 'N', %s, %s, NOW(), %s, %s, %s, %s, %s, '', 0, 'final', NULL)
                """,
                (
                    report_id,
                    item["code"],
                    item["name"],
                    LAB_NAME,
                    item["unit"],
                    item["value"],
                    item["range"],
                    item["abnormal"],
                ),
            )
            inserted += 1

        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def process_order_files(layout):
    orders_dir = Path(layout["edi_base"]) / "orders"
    inbox_dir = Path(layout["edi_base"]) / "inbox"
    if not orders_dir.exists():
        return

    for order_path in sorted(orders_dir.glob("*.txt")):
        conn = None
        try:
            order_text = read_text(order_path)
            result_text, result_rows, control_id = build_result_message(order_text)
            patient, _ = parse_order_message(order_text)

            conn = connect_db(layout)
            try:
                order_id = find_latest_order_id(conn, patient["fname"], patient["lname"])
                if not order_id:
                    log(f"  ! Waiting for OpenEMR order record for {order_path.name}")
                    continue

                result_path = inbox_dir / f"RES_{order_path.name}"
                write_text(result_path, result_text)
                inserted = publish_openemr_results(conn, order_id, control_id, result_rows)

                if inserted:
                    order_path.unlink(missing_ok=True)
                    result_path.unlink(missing_ok=True)
                    log(f"  ✓ Processed {order_path.name} and inserted {inserted} result row(s)")
            finally:
                if conn and conn.open:
                    conn.close()
        except Exception as exc:
            log(f"  ! Could not process {order_path.name}: {exc}")


def run_loop(layout):
    log("Starting Hit4EMR lab simulator...")
    log(f"  OpenEMR root: {layout['openemr_root']}")
    log(f"  EDI base: {layout['edi_base']}")
    log("")

    while True:
        try:
            process_order_files(layout)
        except Exception as exc:
            log(f"Simulator loop warning: {exc}")
        time.sleep(5)


def install(layout):
    ensure_edi_paths(layout)
    seed_procedure_catalog(layout)
    seed_diagnosis_codes(layout)
    seed_problem_history(layout)
    patch_order_form(layout)
    log("Installation complete.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit4EMR Lab Simulator")
    parser.add_argument("--install", action="store_true", help="Prepare OpenEMR for the student lab")
    args = parser.parse_args(argv)

    layout = discover_layout()

    if args.install:
        install(layout)
        return 0

    run_loop(layout)
    return 0


if __name__ == "__main__":
    if pymysql is None:
        print("PyMySQL is required. Install it with: pip install pymysql")
        raise SystemExit(1)
    raise SystemExit(main())
