# Install Guide

## What this installer does

- detects whether you are using a host install or a Docker sandbox
- prepares the lab data inside OpenEMR
- starts the background simulator so results arrive automatically
- gives the student a guided first-login walkthrough
- keeps the workflow inside OpenEMR first

## Install on Ubuntu

From the repository folder:

```bash
bash install.sh
```

If you are installing on a server where OpenEMR already exists, the script will use host mode and connect to that installation.

If you are using Docker, place the compose stack beside this repo before running the installer.

At the start, the installer shows a simple choice screen:

- AWS EC2 Ubuntu host install
- local Ubuntu host install like ElCurioso
- Docker sandbox on the same machine, if the compose stack is present

## What students should expect

- a short preflight check
- a visible progress indicator while the lab starts
- a self-check for the one Python package it needs
- a setup confirmation after the lab data is inserted
- a background simulator log called `hit4emr-simulator.log`
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
4. Open the new procedure order form.
5. In `Default Procedure Type`, choose `Laboratory Test`.
6. Fill the required order fields:
   - `Primary Diagnosis`
   - `Specimen Collection Date`, if the field appears
   - `Billing Type`, if the field appears
7. Pick one of the sample tests.
8. Save the order.
9. Wait a few seconds.
10. Check `Procedures -> Pending Review -> Procedure Results`.

## How the form works

The exact OpenEMR screen can vary a little by version, but the lab order flow should still follow the same pattern:

- choose `Laboratory Test`
- enter a diagnosis
- complete any required collection or billing fields
- select the test
- save the order

If a field is marked as required, students should fill it before clicking save.

## Why this works better

- it uses OpenEMR's normal workflow first
- it simulates an external lab instead of hiding the lab process
- it teaches interoperability in a way students can follow
- it starts the simulator automatically so students do not need a second command
- it is less confusing for first-time users

## If something goes wrong

- If Docker is missing, install Docker first or use host mode.
- If the OpenEMR install is not found, set the relevant environment variables in `ontario_lab_turnkey.py`.
- If the result does not show up, check the OpenEMR order and result paths first.
