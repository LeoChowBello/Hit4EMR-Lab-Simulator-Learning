# Hit4EMR Lab Simulator Learning

This repo is the student-facing version of the lab.

The goal is simple:

- teach interoperability to novice informatics students
- use OpenEMR's real workflow first
- simulate an external LIS/OLIS-style lab server
- keep the experience guided and fun for first-time users

## What the lab teaches

- how lab orders move through OpenEMR
- how an external lab can receive an order and return a result
- how LOINC codes map lab tests
- where students should look inside OpenEMR for results

## Supported setup

- OpenEMR 7.0.2+
- OpenEMR 8.x
- Ubuntu EC2 host installs
- Docker-based sandbox installs when the compose stack is present

## Student workflow

1. Log into OpenEMR.
2. Open the patient chart.
3. Go to `Encounter -> Orders -> Procedure Orders`.
4. Open the new procedure order form.
5. In `Default Procedure Type`, choose `Laboratory Test`.
6. Fill the required order fields on the form:
   - `Primary Diagnosis`
   - `Specimen Collection Date`, if the field appears
   - `Billing Type`, if the field appears
7. Choose one of the sample tests.
8. Save the order.
9. Wait a few seconds.
10. Check `Procedures -> Pending Review -> Procedure Results`.

## Sample lab tests

- `6690-2` WBC
- `718-7` Hemoglobin
- `1558-6` Glucose (Fasting)
- `3016-3` TSH
- `2093-3` Total Cholesterol
- `4548-4` Hemoglobin A1c

## Sample diagnosis codes

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

## Quick start

If you are on an Ubuntu server with OpenEMR already installed:

```bash
bash install.sh
```

If you are using Docker, place the compose stack beside this repo and then run the same installer.

When the installer starts, it will ask you to choose the setup that matches your machine:

- AWS EC2 Ubuntu host install
- local Ubuntu host install like ElCurioso
- Docker sandbox on the same machine, if a compose stack is present

## What the installer does

- prepares the EDI folders
- seeds the sample lab tests
- seeds the sample diagnosis codes
- relaxes a few training-only order checks
- auto-fixes the one Python package it needs if possible
- starts the background simulator so results flow back automatically
- shows a guided first-login walkthrough

## Notes

This repo is not trying to fit every OpenEMR installation ever made.
It is aimed at the common student sandbox paths we want to support well.
