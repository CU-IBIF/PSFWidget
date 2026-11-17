# Validation Protocol <ID>: <title>

| | |
|---|---|
| Protocol ID and version | <VAL-0n>, v<1.0> |
| Software version under test | PSFWidget <x.y.z> (`psfwidget --version`), commit <sha> |
| Requirements | <IDs from docs/PLAN.md> |
| Author / date | |
| Approved before execution by (facility manager) / date | |

## 1. Objective

<What this study shows, in one or two sentences.>

## 2. Scope

<Instruments, objectives, channels, formats, and users covered; what is out of scope.>

## 3. Materials

<Bead slide (product and catalogue number), instruments and settings, computers, software versions, reference tool versions.>

## 4. Method

<Numbered steps, including acquisition settings, how PSFWidget is run (command or GUI steps), and where data and results are stored (with a SHA-256 manifest).>

## 5. Acceptance criteria

<Quantitative criteria, copied from docs/VV_PLAN.md §2.4, fixed before execution.>

## 6. Analysis

<Tool and command used (e.g. `tools/method_comparison.py`), statistics, and how outliers are handled. No post-hoc changes without a recorded deviation.>

## 7. Records

<Test record (templates/TEST_RECORD_TEMPLATE.md), raw data manifest, outputs, anomalies (templates/ANOMALY_LOG.md).>

## 8. Deviations

Any deviation from this protocol is recorded in the test record with its reason and impact, and approved by the facility manager.
