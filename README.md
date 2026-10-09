# FK09 Vehicle Dynamics and Lap Simulation

The full simulation tool from T. Zacharelis' thesis (`ref/2023-Zacharelis-VehDynPerfSim (1).pdf`), written in plain Python,
plus a tool that turns simulated event times into 2026 Michigan competition points. Use it to set goals and justify decisions
for the next car.

## Run it

```
uv sync
uv run jupyter lab lapsim.ipynb
```

Edit the car in `settings.json` (tire, suspension, brakes, correlation, effects, car), then Run All Cells. `lapsim.load_car()` reads it.

## Files

| File | What it is |
|---|---|
| `settings.json` | Every car setting and effect switch, read by part 2 of the notebook |
| `lapsim.ipynb` | The notebook: run any part of the model, see times, points, goals and what-ifs |
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

## Effects and assumptions (`lapsim.Effects`)

Switches on `Car.effects`, the `effects` section of `settings.json`. `true` = modelled, `false` = neglected. Defaults are the thesis model, so nothing changes until
you flip one. `effects_study(car, run)` flips them one at a time and shows what each is worth.

| Switch | Default | Off means | Thesis |
|---|---|---|---|
| `aero_ride_height` | on | ride heights stay at the static value; the suspension does not move the aero | 6.3, 7.2 |
| `aero_anti_features` | on | all weight transfer goes through the heave springs (no anti-dive / lift / squat) | 7.2.1 |
| `aero_roll`, `aero_yaw` | on | roll / yaw taken as zero in the aero | 7.3, 8.3.3 |
| `aero_downforce`, `aero_drag`, `rolling_resistance` | on | that force is zero | 2.3, 2.8 |
| `longitudinal_weight_transfer` | on | no load moves between axles under acceleration / braking | 3.2.2, 3.3.2 |
| `lateral_weight_transfer` | **off** | on: load moves to the outer tires in corners (roll centres, ARBs), so load sensitivity costs grip. The thesis lap sim does not do this | 8.3.2.3 |
| `tire_load_sensitivity` | on | constant mu (measured value, or the value at average static tire load for Pacejka) | 2.4 |
| `tire_camber` | on | static camber ignored in the Pacejka model | 8.3.3 |

The aero switches only matter with `aero_mode="sensitivity"` or `"map"`; in `"constant"` mode the aero does not depend on the car state.
Not switchable: the apex drag correction (4.3.1.2; without it the lap solver cannot hold apex speed), and the thesis assumptions that need new
inputs rather than a switch (shift and clutch delay, throttle lift, camber gain, aero side force, varying Ackermann, rear toe, bump steer, compliance).

In `settings.json` the `effects` section is first, and any setting that stops mattering under an assumption carries an `UNUSED IF ...` note.

## Mode decoupled suspension (heave and roll shocks)

`suspension.type_front` and `suspension.type_rear` choose `"corner"` (default: spring and damper at each wheel, plus ARB) or `"decoupled"` (one heave spring/damper and one roll spring/damper per axle; the roll element also takes warp). Each axle is described by a 2x2 wheel-space stiffness `[[(kh+kr)/2, (kh-kr)/2], [(kh-kr)/2, (kh+kr)/2]]`: `kh` is the heave wheel rate (both wheels move together), `kr` the roll wheel rate (wheels move oppositely). Corner shocks tie both to the corner spring (`kh = kw`, `kr = kw + ARB`). Decoupled sets `kh = k_heave / (2 MR_h^2)` and `kr = k_roll / (2 MR_r^2)`; the 2 is there because one element serves both wheels. Damping follows the same split (heave damper sees the mean wheel velocity, roll damper the half difference). Effects: heave stiffness (ride height, pitch) comes only from the heave element; roll stiffness, roll gradient and the front/rear roll split come only from the roll element and ignore the ARB; the 7-post matrix and damper curves use the same split. Pitch still depends on the heave elements, so it stays coupled to heave. With `"corner"` on both axles nothing changes from before.

## Placeholders

Everything marked `PLACEHOLDER` in `lapsim.py` is made up: the 18 Pacejka tire parameters `a0 ... a17` (and the aligning moment `c0 ... c17`) and the aero map (`AeroMap.placeholder()`). The other defaults are the thesis baseline car (NTUA P19). Replace all of it with FK09 data.

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

## Engine and endurance pace

- **Engine**: `settings.json` `car.engine_curve` points at `ref/dyno_curves.csv` (6th gear, wheel power, RPM and ft-lb). `load_dyno_curve` smooths it and resamples it every 100 rpm. The
  torque is referred to engine rpm (wheel power / engine rpm), so it is used in every gear through the real primary, final and gear ratios, and `engine_includes_losses` stops
  `drivetrain_efficiency` being applied a second time. Below the first dyno point (5.6k rpm) the clutch slips and the first torque value is held.
- **Endurance pace**: `correlation.endurance_pace` (1.599) multiplies every endurance lap time in `run_all_events`. It was set so endurance places where the sim places in acceleration and autocross
  (30th / 75 and 36th / 75, about the 43rd percentile; skidpad is left out because the placeholder tires put it 1st), giving 20th of 44 finishers. Lap fade alone is only 1.04 to 1.06, so most of the value
  covers how far the digitized endurance track and the placeholder data are from reality. Re-set it when they are replaced. `event_placements()` shows where the sim times place in the 2026 field.
