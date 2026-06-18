#!/usr/bin/env python3
"""
Hit4EMR Lab Turnkey: version-universal mocklab

The script discovers the OpenEMR Docker layout from docker-compose, inserts the
Ontario Reference Lab rows, patches the procedure-order validation, and then
runs the lightweight Flask watcher that turns outgoing orders into inbound
results.
"""

import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

import pymysql
from flask import Flask

from config_discovery import discover_config

try:
    DISCOVERED = discover_config()
except Exception as exc:
    print(f"Configuration discovery failed: {exc}")
    print("Run this from the same directory as docker-compose*.yml")
    sys.exit(1)

CONFIG = {
    "LAB_NAME": "Ontario Reference Lab",
    "PORT": 5001,
    "CONTAINER_NAME": DISCOVERED["CONTAINER_NAME"],
    "MYSQL_CONTAINER": DISCOVERED["MYSQL_CONTAINER"],
    "MYSQL_SERVICE": DISCOVERED["MYSQL_SERVICE"],
    "EDI_BASE": DISCOVERED["EDI_BASE"],
    "DB_CONFIG": DISCOVERED["DB_CONFIG"],
}

CATALOG = {
    "6690-2": dict(name="WBC", unit="x10^9/L", low=4.0, high=11.0),
    "718-7": dict(name="Hemoglobin", unit="g/L", low=120.0, high=175.0),
    "1558-6": dict(name="Glucose (Fasting)", unit="mmol/L", low=3.6, high=6.0),
    "3016-3": dict(name="TSH", unit="mIU/L", low=0.32, high=4.00),
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


def _read_sqlconf_text():
    db_config = CONFIG["DB_CONFIG"]
    if os.path.exists(db_config):
        with open(db_config, "r", encoding="utf-8") as handle:
            return handle.read()

    result = subprocess.run(
        f"docker exec {CONFIG['CONTAINER_NAME']} cat {db_config}",
        shell=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not read sqlconf.php")
    return result.stdout


def _parse_sqlconf(text):
    def grab(key):
        match = re.search(
            rf"\${key}\s*=\s*['\"]([^'\"]*)['\"]",
            text,
        )
        if not match:
            raise ValueError(f"missing ${key} in sqlconf.php")
        return match.group(1)

    return {
        "user": grab("login"),
        "password": grab("pass"),
        "database": grab("dbase"),
    }


def _mysql_hosts():
    hosts = []
    if os.path.exists("/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php"):
        hosts.extend([CONFIG["MYSQL_SERVICE"], CONFIG["MYSQL_CONTAINER"], "127.0.0.1", "localhost"])
    else:
        ip = subprocess.run(
            f"docker inspect -f '{{{{range.NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' {CONFIG['MYSQL_CONTAINER']}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if ip:
            hosts.append(ip)
        hosts.extend([CONFIG["MYSQL_SERVICE"], CONFIG["MYSQL_CONTAINER"], "127.0.0.1", "localhost"])

    seen = set()
    ordered = []
    for host in hosts:
        if host and host not in seen:
            ordered.append(host)
            seen.add(host)
    return ordered


def get_db():
    sqlconf = _parse_sqlconf(_read_sqlconf_text())
    last_error = None

    for host in _mysql_hosts():
        try:
            conn = pymysql.connect(
                host=host,
                user=sqlconf["user"],
                password=sqlconf["password"],
                database=sqlconf["database"],
                connect_timeout=5,
                read_timeout=10,
                write_timeout=10,
            )
            print(f"  ✓ Connected to database via {host}")
            return conn
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Could not connect to MySQL: {last_error}")


def auto_configure():
    print("Configuring OpenEMR database...")
    conn = get_db()
    cur = conn.cursor()
    try:
        orders_path = f"{CONFIG['EDI_BASE']}/orders"
        results_path = f"{CONFIG['EDI_BASE']}/inbox"

        cur.execute(
            """
            INSERT IGNORE INTO procedure_providers
              (name, npi, active, direction, protocol, orders_path, results_path)
            VALUES (%s, %s, 1, %s, %s, %s, %s)
            """,
            (CONFIG["LAB_NAME"], "123456", "B", "FS", orders_path, results_path),
        )

        cur.execute(
            "SELECT ppid FROM procedure_providers WHERE name=%s",
            (CONFIG["LAB_NAME"],),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("procedure provider insert failed")
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

        cur.execute(
            "SELECT COUNT(*) FROM procedure_providers WHERE name=%s",
            (CONFIG["LAB_NAME"],),
        )
        provider_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM procedure_type WHERE lab_id=%s AND procedure_type='ord'",
            (lab_id,),
        )
        test_count = cur.fetchone()[0]

        print(f"  ✓ Database configured: {provider_count} provider(s), {test_count} test(s)")
        if provider_count < 1 or test_count < len(CATALOG):
            raise RuntimeError(
                f"Lab verification failed: providers={provider_count}, tests={test_count}, expected_tests={len(CATALOG)}"
            )
    except Exception as exc:
        conn.rollback()
        print(f"ERROR during database configuration: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def ensure_edi_paths():
    print("Ensuring EDI folders are writable...")
    base = CONFIG["EDI_BASE"]
    targets = [f"{base}/orders", f"{base}/inbox"]

    if os.path.exists("/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php"):
        for path in targets:
            os.makedirs(path, exist_ok=True)
        subprocess.run(["chown", "-R", "apache:apache", base], check=False)
        subprocess.run(["chmod", "-R", "ug+rwX", base], check=False)
    else:
        for path in targets:
            subprocess.run(f"docker exec {CONFIG['CONTAINER_NAME']} mkdir -p {path}", shell=True, check=False)
        subprocess.run(
            f"docker exec {CONFIG['CONTAINER_NAME']} chown -R apache:apache {base}",
            shell=True,
            check=False,
        )
        subprocess.run(
            f"docker exec {CONFIG['CONTAINER_NAME']} chmod -R ug+rwX {base}",
            shell=True,
            check=False,
        )


def seed_diagnosis_codes():
    print("Seeding diagnosis codes...")
    conn = get_db()
    cur = conn.cursor()
    try:
        codes = [code for code, _ in DIAGNOSIS_CATALOG]
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
        print(f"  ✓ Diagnosis code rows available: {cur.fetchone()[0]}")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR during diagnosis code seeding: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def seed_problem_history():
    print("Seeding patient problem history...")
    conn = get_db()
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
            cur.execute(
                "UPDATE lists SET diagnosis=%s WHERE id=%s",
                (f"ICD10:{code}", row_id),
            )
            updated += 1
        conn.commit()
        print(f"  ✓ Problem history rows updated: {updated}")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR during problem history seeding: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def patch_php():
    container = CONFIG["CONTAINER_NAME"]
    target = "/var/www/localhost/htdocs/openemr/interface/forms/procedure_order/common.php"
    backup = target + ".bak"

    print("Patching OpenEMR validation logic...")
    subprocess.run(f"docker exec {container} cp {target} {backup}", shell=True, check=False)

    patch_script = """<?php
$f = '/var/www/localhost/htdocs/openemr/interface/forms/procedure_order/common.php';
$c = file_get_contents($f);
$c = str_replace("if ($_POST['form_provider_id'] + 0 < 1)", "if (false && $_POST['form_provider_id'] + 0 < 1)", $c);
$c = str_replace('if ($_POST["form_provider_id"] + 0 < 1)', 'if (false && $_POST["form_provider_id"] + 0 < 1)', $c);
$c = str_replace('if ($diag_flag === 0)', 'if (false && $diag_flag === 0)', $c);
$c = str_replace("if (!$_POST['form_date_collected'] && !$_POST['form_order_psc'])", "if (false && !$_POST['form_date_collected'] && !$_POST['form_order_psc'])", $c);
$c = str_replace('if (!$_POST["form_date_collected"] && !$_POST["form_order_psc"])', 'if (false && !$_POST["form_date_collected"] && !$_POST["form_order_psc"])', $c);
$c = str_replace("if (empty($_POST['form_billing_type']))", "if (false && empty($_POST['form_billing_type']))", $c);
$c = str_replace('if (empty($_POST["form_billing_type"]))', 'if (false && empty($_POST["form_billing_type"]))', $c);
file_put_contents($f, $c);
?>"""

    patch_path = os.path.join("/tmp", "ontario_lab_patch.php")
    with open(patch_path, "w", encoding="utf-8") as handle:
        handle.write(patch_script)

    subprocess.run(f"docker exec -i {container} php < {patch_path}", shell=True, check=False)
    check = subprocess.run(
        f"docker exec {container} php -l {target}",
        shell=True,
        capture_output=True,
        text=True,
    )
    if "No syntax errors detected" not in check.stdout:
        subprocess.run(f"docker exec {container} cp {backup} {target}", shell=True, check=False)
        raise RuntimeError("PHP patch failed and was rolled back")


def publish_openemr_results(order_id, control_id, result_rows):
    print("Publishing results into OpenEMR Procedure Results...")
    if not result_rows:
        return

    conn = get_db()
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
                    CONFIG["LAB_NAME"],
                    item["unit"],
                    item["value"],
                    item["range"],
                    item["abnormal"],
                ),
            )
            inserted += 1

        conn.commit()
        print(f"  ✓ Procedure Results rows inserted: {inserted}")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR during OpenEMR result publication: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def process_logic():
    orders_dir = f"{CONFIG['EDI_BASE']}/orders"
    inbox_dir = f"{CONFIG['EDI_BASE']}/inbox"

    try:
        if os.path.exists(orders_dir):
            files = [name for name in os.listdir(orders_dir) if name.endswith(".txt")]
        else:
            result = subprocess.run(
                f"docker exec {CONFIG['CONTAINER_NAME']} ls {orders_dir} 2>/dev/null || true",
                shell=True,
                capture_output=True,
                text=True,
            )
            files = [name for name in result.stdout.splitlines() if name.endswith(".txt")]
    except Exception:
        return

    for fname in files:
        order_path = os.path.join(orders_dir, fname)
        try:
            result_rows = []
            if os.path.exists(order_path):
                with open(order_path, "r", encoding="utf-8") as handle:
                    raw = handle.read()
            else:
                result = subprocess.run(
                    f"docker exec {CONFIG['CONTAINER_NAME']} cat {order_path}",
                    shell=True,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    continue
                raw = result.stdout

            lines = raw.splitlines()
            msh = lines[0].split("|") if lines else []
            control_id = msh[9] if len(msh) > 9 else "SIM"
            try:
                order_id = int(control_id)
            except Exception:
                order_id = int(os.path.splitext(fname)[0])
            pid = next((line for line in lines if line.startswith("PID|")), "PID|1||||Unk^Pat||19800101|M")
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

            response = f"MSH|^~\\&|ONTARIOLAB|LAB|OPENEMR|CLINIC|{timestamp}||ORU^R01|{control_id}|D|2.3\n{pid}\n"
            for sequence, code in re.findall(r"OBR\|(\d+)\|.*?\|\|(.*?)\^", raw):
                response += f"OBR|{sequence}|{control_id}||{code}^|||{timestamp}|||||||||||F\n"
                meta = CATALOG.get(code, {"name": "Test", "unit": "units", "low": 0, "high": 100})
                value = round(random.uniform(meta["low"], meta["high"]), 1)
                result_rows.append(
                    {
                        "sequence": int(sequence),
                        "code": code,
                        "name": meta["name"],
                        "unit": meta["unit"],
                        "value": value,
                        "range": f'{meta["low"]}-{meta["high"]}',
                        "abnormal": "N",
                    }
                )
                response += (
                    f'OBX|1|NM|{code}^{meta["name"]}^LN||{value}|{meta["unit"]}|'
                    f'{meta["low"]}-{meta["high"]}|N|||F\n'
                )

            result_path = os.path.join(inbox_dir, f"RES_{fname}")
            if os.path.exists(inbox_dir):
                with open(result_path, "w", encoding="utf-8") as handle:
                    handle.write(response)
            else:
                subprocess.run(
                    f"docker exec {CONFIG['CONTAINER_NAME']} bash -lc 'cat > {result_path}'",
                    input=response,
                    text=True,
                    shell=True,
                    check=False,
                )

            publish_openemr_results(order_id, control_id, result_rows)
            if os.path.exists(result_path):
                os.remove(result_path)

            if os.path.exists(order_path):
                os.remove(order_path)
            else:
                subprocess.run(f"docker exec {CONFIG['CONTAINER_NAME']} rm {order_path}", shell=True, check=False)
        except Exception:
            pass


def watcher():
    while True:
        process_logic()
        time.sleep(5)


app = Flask(__name__)


@app.route("/")
def home():
    return "<h1>Ontario Lab Active ✅</h1>"


if __name__ == "__main__":
    if "--install" in sys.argv:
        print("\n🚀 MOCKLAB UNIVERSAL INSTALL\n")

        in_container = os.path.exists("/var/www/localhost/htdocs/openemr/sites/default/sqlconf.php")

        print("  1. Creating EDI directories...")
        ensure_edi_paths()

        print("  2. Configuring OpenEMR database...")
        auto_configure()

        print("  3. Seeding diagnosis codes...")
        seed_diagnosis_codes()

        print("  4. Seeding patient problem history...")
        seed_problem_history()

        print("  5. Patching validation logic...")
        patch_php()

        print("\n🎉 Turnkey install complete.")
        print(f"Mocklab is now configured for OpenEMR in {CONFIG['CONTAINER_NAME']}")
        print("The simulator container starts automatically with Docker Compose.")
        sys.exit(0)

    print(f"\n🧪 Starting Ontario Lab Simulator on port {CONFIG['PORT']}...")
    print(f"   Container: {CONFIG['CONTAINER_NAME']}")
    print(f"   Monitoring: {CONFIG['EDI_BASE']}/orders")
    print(f"   Writing results to: {CONFIG['EDI_BASE']}/inbox\n")
    threading.Thread(target=watcher, daemon=True).start()
    app.run(host="0.0.0.0", port=CONFIG["PORT"])
