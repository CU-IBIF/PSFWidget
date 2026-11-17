# Validation protocols (drafts)

Draft protocols for VAL-01 to VAL-07 (docs/VV_PLAN.md §2.4), prepared for
review. Each must be completed (items marked **TBD**) and approved by the
facility manager **before** the study starts, then executed and recorded with
the templates in `../templates/`. Related: `../GUI_TEST_SCRIPT.md` (T11.4) and
the backup restore drill (`psfwidget track restore-check`, T11.3).

| Protocol | Study | Tool |
|---|---|---|
| [VAL-01](VAL-01.md) | Method comparison with MetroloJ_QC / PSFj | `tools/method_comparison.py` |
| [VAL-02](VAL-02.md) | Known-perturbation sensitivity | reports, charts |
| [VAL-03](VAL-03.md) | Repeatability and reproducibility | `tools/repeatability.py` |
| [VAL-04](VAL-04.md) | Theoretical comparison sanity | `psfwidget track export` |
| [VAL-05](VAL-05.md) | Parallel-run field trial | scheduled runs, `psfwidget report audit` |
| [VAL-06](VAL-06.md) | Usability | GUI |
| [VAL-07](VAL-07.md) | Report fitness | `psfwidget verify` |
