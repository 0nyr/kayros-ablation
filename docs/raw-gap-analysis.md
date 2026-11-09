# Frozen raw-gap target figures

`analysis/raw_gap_plots.py` renders separate attainment panels for the frozen main targets of 0%, 1% and 5%. It accepts a completed wave and its summary, binds each checked envelope to the scheduled task and recorded run identity, verifies completion and event/native artifact digests, and checks the installed v0.11 identity. It performs no solver search or new route-cost evaluation. Existing 1% pilot figures remain unchanged.

```bash
python analysis/raw_gap_plots.py --summary /path/to/analysis/summary.json --wave /path/to/wave --output /path/to/analysis/raw-gap-targets
```

For a checked cost `z` and frozen reference `r`, attainment means `(z - r) / r <= target`. An incumbent below the reference attains all 3 targets. A missing incumbent attains none. Valid incumbents persist after a resource failure, and every scheduled cell remains in the denominator. The script averages trials within each instance before averaging instances. Candidate production timestamps use the common process-launch clock, with no post-horizon clamping or external validation time.

The output contains PNG, PDF and `raw-gap-plot-data.json`. The JSON preserves every plotted coordinate, scheduled task/run association, checked envelope, reference value and trial population. It records hashes of the summary, wave, frozen analysis config, reference manifest, per-cell identity/result/events, plot implementation and reused identity/plot-style code. Colors follow the existing shared pilot/main palette. These are main-analysis outputs, not additional pilot-completion or launch gates.
