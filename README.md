# FK09 Vehicle Dynamics and Lap Simulation

The full simulation tool from T. Zacharelis' thesis (`ref/2023-Zacharelis-VehDynPerfSim (1).pdf`), written in plain Python,
plus a tool that turns simulated event times into 2026 Michigan competition points. Use it to set goals and justify decisions
for the next car.

## Run it

```
uv sync
uv run jupyter lab lapsim.ipynb
```

Edit the car in part 2 of the notebook, then Run All Cells.

## Files

| File | What it is |
|---|---|
| `lapsim.ipynb` | The notebook: edit the car, run any part of the model, see times, points, goals and what-ifs |
| `lapsim.py` | All the code in one file, in thesis order (see the list at the top of the file) |
| `tracks/autocross.csv`, `tracks/endurance.csv` | Track centerlines (x, y in metres) traced from the maps in `ref/track images/` |
| `tracks/digitize_tracks.py` | The script that traced them; `digitize_check.png` shows the result over the map |
| `data/results_2026/` | 2026 Michigan results used to score the sim |

## What is in `lapsim.py`

| Thesis | Functions |
|---|---|
| 2.1 - 2.8 vehicle, tires, aero, powertrain, forces, weight transfer | `Car`, `Tire`, `Suspension`, `Brakes`, `Correlation`, `vertical_loads`, `tire_*_limit`, `engine_force` |
| 2.7 shifting model | `shift_table` |
| 2.9 steering, braking, throttle | `steering_angles`, `brake_pressure`, `throttle_position` |
| 2.10 GG / GGV diagram | `ggv_map`, `plot_ggv` |
| 3.2 - 3.4 acceleration, braking, cornering (time and distance solvers) | `run_acceleration`, `run_braking`, `run_cornering` |
| 4.2 track model: 4 import options, filtering, fine mesh, apexes | `track_from_radius_length`, `track_from_gps`, `track_from_xy`, `track_from_accel_speed`, `filter_radius`, `fine_mesh` |
| 4.3 LapSim: apexes, accelerate / brake to apexes, braking points | `simulate_lap` |
| 4.4 driven channels and KPIs | `driven_channels`, `lap_kpis`, `plot_lap`, `plot_lap_map` |
| 4.5 correlation | `Correlation`, `correlation_study`, `fit_correlation`, `compare_traces` |
| 5 suspension: rates, quarter car, damping, sweeps, 7-post rig (the 7x7 suspension matrix) | `suspension_rates`, `quarter_car`, `damping_coefficients`, `damping_curves`, `parameter_sweep`, `seven_post_free_response` |
| 6 aero map: viewer, envelopes, static ride height | `AeroMap`, `plot_aero_map`, `aero_envelope`, `static_ride_height_sweep` |
| 7 aero map in the simulation: ride height convergence, anti features, roll and yaw | `aero_mode="map"` on the `Car`, used by every scenario and the lap simulation |
| 8 yaw moment diagram, Pacejka tire | `yaw_moment_diagram`, `ymd_kpis`, `ymd_speed_sensitivity` |
| FSAE rules D.9 - D.12 and 2026 results | `points_2026`, `event_points`, `goal_times`, `compare_designs` |

`thesis_checks()` compares the code with the numbers printed in the thesis (rates, damping, brake pressure, skidpad time, 7-post KPIs):
all agree within 1 %.

## Placeholders

Everything marked `PLACEHOLDER` in `lapsim.py` is made up: the 18 Pacejka tire parameters `a0 ... a17` (and the aligning moment `c0 ... c17`), the engine
curve, and the aero map (`AeroMap.placeholder()`). The other defaults are the thesis baseline car (NTUA P19). Replace all of it with FK09 data.

## Where the code differs from the thesis

- **Elastic weight transfer** (eq 8-14): the thesis prints the axle's suspended mass; the code uses the whole suspended mass, which is what the
  roll moment acts on. `lateral_weight_transfer(..., elastic_as_printed=True)` gives the printed form.
- **Single bump rate** (eq 5-11): the printed formula adds a reciprocal stiffness, so the thesis table shows the heave rate. The code uses the
  series-parallel combination the text describes.
- **Dynamic pitch centre** (eq 5-5): used exactly as printed because it reproduces Table 5-1.
- **7-post rig**: dampers are linear by default (mean of the high-speed compression and rebound coefficients), which reproduces the thesis heave KPIs
  within 1 %. `damper="curve"` uses the full damping curve.
- **Anti features from geometry** (eq 7-4, 7-5): the printed inboard-brake formula divides by the braking share; the standard form is used.
- **Throttle position** is shown as 0 % when braking (the printed formula gives large negative numbers). **Brake pressure** only counts the part of the
  deceleration the brakes provide (drag is subtracted).
- **Weight transfer** cannot make an axle load negative: the other axle then carries the whole load.
- **Aligning moment** uses the standard Pacejka '94 form; the thesis does not list those equations.
- **Camber** is the static camber; camber gain and the caster effect are not modelled (the thesis lists them as future work).
- **Select-points start/finish processing** is non-interactive: give the points to remove instead of clicking on a plot.
- The thesis acceleration and lap times depend on its engine data file, which is not available, so only the engine-independent results are checked.

## Other notes

- Tracks were traced from images at about 0.7 m per pixel, so corner radii and lap times are approximate. Replace the CSVs with better centerlines
  or build a track from GPS or logger data (`track_from_gps`, `track_from_accel_speed`).
- Sim endurance times are much faster than real ones (no traffic, mistakes, or driver change). Use the correlation factors with real data.
- Efficiency points are not modelled; `points_2026(..., efficiency=...)` takes a number.
