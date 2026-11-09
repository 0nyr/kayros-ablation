# Main schedule preparation

We have prepared the complete 56-instance roster from the immutable pre-pilot reference manifest and a nonlaunchable inventory of 1,400 logical cells: 56 instances, 5 arms and 5 trial slots. The main full arm has exactly 1 configuration. Its prefix and the common horizon remain unset until the pilot review. The template assigns no hosts, runtime environments or process identities and creates no solver outputs or reservations.

The generated `configs/main-selection.json` records every instance hash and source-reference hash against reference manifest SHA-256 `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`. The paired heuristic seeds are 42, 123, 456, 789 and 2026 at trial IDs 1 to 5. Cold exact arms keep a null search seed and use those IDs as runtime repetitions. `configs/main-schedule-template.json` contains the exact roster and trial mapping with `launchable: false`.

```bash
python scripts/prepare_main_schedule.py --output-dir /path/to/new-preparation-directory
```

The generator checks the independently pinned reference digest and refuses to replace existing preparation artifacts. Its tests verify all 1,400 unique task identities, 25 tasks per instance, 5 arms, 5 trial slots and the paired seed mapping.

## Explicit selection and resource settings

The distributed controller now accepts `--selection-file` and `--expected-count`. Its defaults remain the frozen 12-instance pilot roster. Each future wave copies the actual selection file into `selection.json`, records its source path and SHA-256, and checks every selected instance against the frozen reference manifest before launching cells. A full-panel main invocation must explicitly select the 56-instance roster and use exactly the approved main configuration.

The controller also accepts `--memory-mb`, interpreted as MiB for the process address-space cap, with the existing default of 8,192 MiB. Each node checks the sum of simultaneous core-lane caps plus a 16 GiB reserve against its actual available RAM. The completed 8 GiB pilot retains its original limits and 2 allocation failures. At the author's request, both follow-ups use 16,384 MiB and at most 10 workers per node, requiring 176 GiB available RAM on a fully occupied node. Their results will guide the final cap and concurrency without assuming that a 2-hour run fits the original limit. Per-process virtual-memory observations supplement RSS because the enforced limit is address space.

Capacity must be checked again after the pilot review. The official grvingt snapshot at 11:00 CET on 2026-11-09 showed about 41 clearly usable hosts including the 21 allocated to the original pilot, subject to job completion and standby availability. At 10 workers per host, this gives 410 slots for 1,400 main cells and hence 4 rounds. A provisional 7,200-second horizon would require up to 8 hours plus setup, audit and transfer margin. At least 47 hosts would reduce that bound to 3 rounds. These figures are capacity scenarios, not reservations or a fixed main-campaign plan. The two live follow-ups occupy separate 33-host and 6-host allocations.

## Recovery and launch boundary

The read-only recovery audit and the manual retry policy are described in [recovery-protocol.md](recovery-protocol.md). Main preparation preserves the frozen roster and exact inventory without selecting a prefix, finalizing a horizon or launching the main campaign. The concrete main manifest must record the chosen configuration, matched resource limits, runtime/source identities, selection provenance and the attempt-selection policy before dispatch.

After both follow-ups finish, `scripts/prepare_main_proposal.py` binds the complete full-panel summary, its eligible prefix decision and the separate focused horizon review to the frozen references. It produces a concrete 1,400-cell proposal with one selected full-arm prefix, paired trial seeds, a common horizon within the reviewed range, the same memory cap and an explicit capacity calculation. The proposal retains `launchable: false` and the author-review requirement. It creates no reservation, runtime environment or solver output. Evidence files and the generator receive SHA-256 records, and an existing proposal is never overwritten.

`analysis/resource_comparison.py` compares the two complete 1-hour panels using matched instance/configuration/trial identities and common input, seed, reference and horizon values. It keeps separate resource-budget columns and all failed observations. `analysis/resource_review.py` separately displays observed individual-process virtual-memory peaks with explicit sampling coverage. Missing observations remain missing, and worker and child address spaces are never added together. These artifacts support the final resource recommendation without attributing every cross-wave timing change to memory alone.
