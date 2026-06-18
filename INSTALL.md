# Install Guide

## What this installer does

- detects whether you are using a host install or a Docker sandbox
- prepares the lab data inside OpenEMR
- gives the student a guided first-login walkthrough
- keeps the workflow inside OpenEMR first

## Install on Ubuntu

From the repository folder:

```bash
bash install.sh
```

If you are installing on a server where OpenEMR already exists, the script will use host mode and connect to that installation.

If you are using Docker, place the compose stack beside this repo before running the installer.

## What students should expect

- a short preflight check
- a visible progress indicator while the lab starts
- a setup confirmation after the lab data is inserted
- a short walkthrough of where to click in OpenEMR

## Login

- Username: `admin`
- Password: `pass`

## Available lab tests

- `6690-2` WBC
- `718-7` Hemoglobin
- `1558-6` Glucose (Fasting)
- `3016-3` TSH
- `2093-3` Total Cholesterol
- `4548-4` Hemoglobin A1c

## Available diagnosis codes

- `R79.89` Other specified abnormal findings of blood chemistry
- `D64.9` Anemia, unspecified
- `E11.9` Type 2 diabetes mellitus without complications
- `E78.5` Hyperlipidemia, unspecified
- `E03.9` Hypothyroidism, unspecified
- `R73.03` Prediabetes
- `R53.83` Other fatigue
- `N18.9` Chronic kidney disease, unspecified
- `Z13.1` Encounter for screening for diabetes mellitus
- `Z13.220` Encounter for screening for lipoid disorders

## Student order path

Use this in the chart:

1. Open the patient chart.
2. Open `Encounter`.
3. Choose `Orders -> Procedure Orders`.
4. Create a new order.
5. Pick one of the sample tests.
6. Save the order.
7. Wait a few seconds.
8. Check `Procedures -> Pending Review -> Procedure Results`.

## Why this works better

- it uses OpenEMR's normal workflow first
- it simulates an external lab instead of hiding the lab process
- it teaches interoperability in a way students can follow
- it is less confusing for first-time users

## If something goes wrong

- If Docker is missing, install Docker first or use host mode.
- If the OpenEMR install is not found, set the relevant environment variables in `ontario_lab_turnkey.py`.
- If the result does not show up, check the OpenEMR order and result paths first.
