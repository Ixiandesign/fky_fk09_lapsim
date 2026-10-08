# ref/converted — machine-readable versions of the ref/ PDFs

Generated 2026-10-08. Originals stay in `ref/`. Read this file first, then grep/read only the part you need.

| Source PDF | Converted | Notes |
|---|---|---|
| `2023-Zacharelis-VehDynPerfSim (1).pdf` (463 pp, MSc thesis "Vehicle Dynamics and Performance Simulation", T. Zacharelis, NTUA, July 2023 — basis for the lapsim) | `zacharelis_2023_vehdyn_perfsim.md` + `zacharelis_headings_index.txt` + `zacharelis_paper_images/` | see below |
| `FSAE_Rules_2027_V1.pdf` | `fsae_rules_2027_v1.md` + `fsae_rules_2027_headings_index.txt` | Markdown, rule IDs (e.g. `D.12.13.3`) are headings; `<!-- page N -->` markers = PDF page |
| `FSAE_2026_MI5_results.pdf` (FSAE Michigan 2026, 43 pp) | `fsae_2026_results_csv/*.csv` (primary) + `fsae_2026_mi5_results.md` (raw text fallback) | CSVs are clean; the .md has missing spaces in team names |

## Zacharelis thesis
- `*_headings_index.txt`: `L<line in md> p<pdf page> heading` — use it to jump to a chapter (e.g. `Read` with offset/limit). File is ~13k+ lines; never read whole.
- Text and tables (e.g. Table 2-1 vehicle parameters: mass 250 kg, WD 49 %, WB 1.53 m, CoG 0.330 m, ...) are real Markdown tables.
- **Equations in the PDF are embedded images.** Every small image (210) was manually transcribed and inserted right after its `![](...)` line as `**[Equation/table image transcribed #N]** $$LaTeX$$`. Figures/plots are summarised as `*[FIG: ...]*`. Many equations are *fragments* (the LHS lives in the surrounding text, the image holds "= ..."), so read the equation with its surrounding paragraph.
- Transcription caveats: made by reading 110-dpi renders; items marked "as printed" reproduce apparent typos/dimensional oddities in the thesis (e.g. curvature eq. #90, `NatFreqSM_R` using `Mass_NSMr`, inboard anti-dive eq. #192). If a number/sign matters for the sim, **verify against the image** in `zacharelis_paper_images/` (file name contains the PDF page, e.g. `...-0032-08.png` = page 32).
- 203 larger images (photos, plots, flowcharts, track maps) are left as image links only; open the PNG if a figure matters. Table of contents / list-of-figures/equations at the end of the md is noise.
- Variable naming convention in the thesis: `Vehicle.<group>.<param>` = inputs, `Model.<group>.<param>` = computed.

## FSAE 2026 results CSVs (`fsae_2026_results_csv/`)
`overall, design, presentation, cost, acceleration, skidpad, autocross, endurance, efficiency, endurance_laptimes, team_info`.
- Header row defined in `extract_results_csv.py`; times in seconds, blank = none, `DNF/DNA` kept as text. Ties show as `"21 T"` in Place.
- Event scoring constants (from page headers): Accel min 4.126 s (cone +2 s, max 150 %); Skidpad min 4.878 s (cone +0.125 s, max 125 %); Autocross min 39.031 s (cone +2 s, off-course +20 s, max 145 %); Endurance min 1420.853 s (cone +2 s, off-course +20 s, max 145 %); Efficiency min/max avg laptime 142.085/206.024 s.
- Endurance laps score table (rule D.12.13.3, PDF p.38, no CSV; columns laps completed / points for lap / cumulative laps score): 0/1/1, 1/2/3, 2/2/5, 3/2/7, 4/2/9, 5/3/12, [driver change], 6/3/15, 7/3/18, 8/3/21, 9/3/24, 10/1/25.
- `endurance_laptimes.csv`: laps left-aligned (blank laps dropped), up to 10 laps; 76 of 77 endurance entries.
- `team_info.csv` is "For Information Only / Not Validated" (engine cylinders/displacement, car weight).
- University of Kentucky = car #100 (rows in every CSV).
- Source PDF has a broken glyph in "Università degli studi di Modena e Reggio Emilia" (fixed in CSVs).

## Scripts
`convert_pdfs.py` (pymupdf4llm → md + images; needs `pip install pymupdf4llm pdfplumber`) and `extract_results_csv.py` (pdfplumber → CSVs). Paths in them are relative to the repo root.
