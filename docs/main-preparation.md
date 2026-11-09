# Main schedule preparation

We have prepared the complete 56-instance roster from the immutable pre-pilot reference manifest and a nonlaunchable inventory of 1,400 logical cells: 56 instances, 5 arms and 5 trial slots. The main full arm has exactly 1 configuration. Its prefix and the common horizon remain unset until the pilot review. The template assigns no hosts, runtime environments or process identities and creates no solver outputs or reservations.

The generated `configs/main-selection.json` records every instance hash and source-reference hash against reference manifest SHA-256 `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`. The paired heuristic seeds are 42, 123, 456, 789 and 2026 at trial IDs 1 to 5. Cold exact arms keep a null search seed and use those IDs as runtime repetitions. `configs/main-schedule-template.json` contains the exact roster and trial mapping with `launchable: false`.

```bash
python scripts/prepare_main_schedule.py --output-dir /path/to/new-preparation-directory
```

The generator checks the independently pinned reference digest and refuses to replace existing preparation artifacts. Its tests verify all 1,400 unique task identities, 25 tasks per instance, 5 arms, 5 trial slots and the paired seed mapping.

## Explicit selection and resource settings

The distributed controller now accepts `--selection-file` and `--expected-count`. Its defaults remain the frozen 12-instance pilot roster. Each future wave copies the actual selection file into `selection.json`, records its source path and SHA-256, and checks every selected instance against the frozen reference manifest before launching cells. A full-panel main invocation must explicitly select the 56-instance roster and use exactly the approved main configuration.

The controller also accepts `--memory-mb`, interpreted as MiB for the process address-space cap, with the existing default of 8,192 MiB. Each node checks the sum of simultaneous core-lane caps plus a 16 GiB reserve against its actual available RAM. We retain the current pilot's 8 GiB cap. A proposed 12 GiB main cap with 14 workers requires at least 184 GiB available per node under this guard and is feasible only where that condition holds. We should choose the cap and concurrency from the final sampled memory trajectories and observed limit outcomes, without assuming that a 2-hour run fits the pilot's limit.

Capacity must be checked again after the pilot review. The official grvingt snapshot at 11:00 CET on 2026-11-09 showed about 41 clearly usable hosts including the 21 allocated to the pilot, subject to job completion and standby availability. At 14 workers per host, this gives 574 slots for 1,400 main cells and hence 3 rounds. A provisional 7,200-second horizon would require up to 6 hours plus setup, audit and transfer margin. These figures are a capacity scenario, not a reservation or a fixed campaign plan.

## Recovery and launch boundary

The read-only recovery audit and the manual retry policy are described in [recovery-protocol.md](recovery-protocol.md). Main preparation preserves the frozen roster and exact inventory without selecting a prefix, finalizing a horizon or launching the main campaign. The concrete main manifest must record the chosen configuration, matched resource limits, runtime/source identities, selection provenance and the attempt-selection policy before dispatch.
