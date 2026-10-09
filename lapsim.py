"""FSAE vehicle dynamics and lap simulation after T. Zacharelis, "Vehicle Dynamics and Performance
Simulation" (NTUA MSc thesis, 2023), plus a tool that turns simulated event times into 2026 Michigan points.

Everything is in SI units (m, s, N, kg, m/s) unless a name says otherwise.
Section numbers in the comments (2.8, 4.3, ...) refer to the thesis.

Contents (in thesis order)
     1. Vehicle parameters        Tire (Pacejka 18 params), Suspension, Brakes, Correlation, Effects, Car   (ch. 2)
     2. Aero                      constant / roll-yaw sensitivity / full aero map with convergence   (2.3, ch. 6, 7)
     3. Forces model              vertical loads, weight transfer, tire limits, engine, drag         (2.4 - 2.8)
     4. Powertrain extras         shifting model                                                     (2.7)
     5. Steering, braking, throttle models                                                           (2.9)
     6. GG / GGV diagram                                                                             (2.10)
     7. Corner speed              pure cornering table, apex table with friction ellipse             (3.4, 4.3.1)
     8. Specific scenarios        acceleration, braking, cornering                                   (ch. 3)
     9. Track model               4 import options, filtering, fine mesh, apexes                     (4.2)
    10. LapSim                    forward / reverse solver, driven channels, KPIs                    (4.3, 4.4)
    11. Suspension dynamics       rates calculator, quarter car, damping, 7-post rig, sweeps         (ch. 5)
    12. Aero map tools            aero viewer, aero envelopes, static ride height decision           (ch. 6)
    13. Yaw moment diagram        full-car steady state, Pacejka, KPIs                               (ch. 8)
    14. Correlation               stepwise correction factors, check against the thesis tables       (4.5)
    15. Competition points        rules formulas + 2026 results                                      (FSAE rules D.9 - D.12)
    16. Design tools              goal times, what-if, effects study
"""
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

G = 9.81
HERE = Path(__file__).parent


# =====================================================================================
# 1. VEHICLE PARAMETERS
#    The defaults are the thesis baseline car (NTUA P19), so results can be checked against the thesis
#    tables. Anything marked PLACEHOLDER is made up. Replace all of it with FK09 data.
# =====================================================================================

@dataclass
class Tire:
    """Tire parameters (thesis Table 2-3 and Chapter 8).

    Longitudinal grip uses the thesis linear load-sensitivity model (eq 2-12, 2-13):
        mux = mux0 + sens * (norm_load * g - Fz_tire)
    Lateral grip uses either
        lateral_model = "pacejka": the 18-parameter Pacejka '94 lateral model a0 ... a17 (Table 8-1, 8-2), or
        lateral_model = "simple" : the same linear model as above with muy.
    The Pacejka parameters below are PLACEHOLDERS. Units: Fz in kN, slip angle and camber in degrees.
    """
    radius: float = 0.199                # m, rolling radius
    rolling_resistance: float = 0.03     # Cr, positive number
    # simple friction model (Table 2-3)
    mux: float = 1.2                     # longitudinal friction coefficient
    mux_norm_kg: float = 50.0            # load at which mux was measured, kg
    mux_sens: float = 1e-4               # change of mux per newton of load below the norm load, 1/N
    muy: float = 1.45                    # lateral friction coefficient (used if lateral_model = "simple")
    muy_norm_kg: float = 50.0
    muy_sens: float = 1e-4
    lateral_model: str = "pacejka"       # "pacejka" or "simple"
    # axle cornering stiffness for the steering model (eq 2-53). None = take it from the Pacejka model.
    cornering_stiffness_front: float = None   # N/deg
    cornering_stiffness_rear: float = None    # N/deg
    # Pacejka '94 lateral force, a0 ... a17 (PLACEHOLDER values)
    a: tuple = (1.5,      # a0  shape factor C
                -100.0,   # a1  load influence on friction (x1000, 1/kN)
                1600.0,   # a2  peak friction coefficient (x1000)
                1000.0,   # a3  peak cornering stiffness, N/deg
                1.5,      # a4  load at peak stiffness, kN
                0.02,     # a5  camber influence on stiffness
                0.0,      # a6  curvature change with load
                -1.0,     # a7  curvature factor E
                0.0,      # a8  load influence on horizontal shift
                0.0,      # a9  horizontal shift
                0.0,      # a10 camber influence on horizontal shift
                0.0,      # a11 vertical shift
                0.0,      # a12 vertical shift at zero load
                0.0,      # a13 camber influence on vertical shift (load)
                0.0,      # a14 camber influence on vertical shift
                0.003,    # a15 camber influence on peak friction
                0.0,      # a16 curvature change with camber
                0.0)      # a17 curvature shift
    # Pacejka '94 self-aligning moment, c0 ... c17 (PLACEHOLDER; the thesis does not list these values)
    c: tuple = (2.4, 0.0, -50.0, 0.0, -30.0, 0.0, 0.0, 0.0, 0.0, -2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def pacejka_fy(a, fz_kn, slip_deg, camber_deg=0.0):
    """Pacejka '94 lateral force [N] (Table 8-2, eq 8-4). Works on numpy arrays."""
    fz = np.asarray(fz_kn, float)
    s = np.asarray(slip_deg, float)
    g = camber_deg
    C = a[0]
    D = fz * (a[1] * fz + a[2]) * (1 - a[15] * g ** 2)
    BCD = a[3] * np.sin(np.arctan(fz / a[4]) * 2) * (1 - a[5] * abs(g))
    B = BCD / (C * D)
    H = a[8] * fz + a[9] + a[10] * g
    V = a[11] * fz + a[12] + (a[13] * fz + a[14]) * g * fz
    x = s + H
    E = (a[6] * fz + a[7]) * (1 - (a[16] * g + a[17]) * np.sign(x))
    Bx = B * x
    return D * np.sin(C * np.arctan(Bx - E * (Bx - np.arctan(Bx)))) + V


def pacejka_mz(c, fz_kn, slip_deg, camber_deg=0.0):
    """Pacejka '94 self-aligning moment [Nm]. Standard c0 ... c17 form (the thesis only says "similar procedure")."""
    fz = np.asarray(fz_kn, float)
    s = np.asarray(slip_deg, float)
    g = camber_deg
    C = c[0]
    D = c[1] * fz ** 2 + c[2] * fz
    BCD = (c[3] * fz ** 2 + c[4] * fz) * (1 - c[6] * abs(g)) * np.exp(-c[5] * fz)
    B = BCD / (C * np.where(D == 0, 1e-9, D))
    H = c[11] * g + c[12] * fz + c[13]
    V = (c[14] * fz ** 2 + c[15] * fz) * g + c[16] * fz + c[17]
    x = s + H
    E = (c[7] * fz ** 2 + c[8] * fz + c[9]) * (1 - c[10] * g)
    Bx = B * x
    return D * np.sin(C * np.arctan(Bx - E * (Bx - np.arctan(Bx)))) + V


def pacejka_peak_mu(a, fz_tire_n, camber_deg=0.0):
    """Peak lateral friction coefficient of one tire for each load in fz_tire_n [N] (largest |Fy|/Fz over slip angle)."""
    slip = np.linspace(0.0, 25.0, 251)[:, None]
    fz = np.asarray(fz_tire_n, float)[None, :] / 1000.0
    fy = pacejka_fy(a, fz, slip, camber_deg)
    return np.max(np.abs(fy), axis=0) / (fz[0] * 1000.0)


@dataclass
class Suspension:
    """Suspension parameters (thesis Table 2-4, 7-1). Thesis baseline values, replace with FK09 data."""
    toe_front: float = 0.0               # deg, static toe (used in the yaw moment diagram)
    camber_front: float = 0.0            # deg, static camber
    camber_rear: float = 0.0
    roll_centre_front: float = 0.0295    # m, roll centre height
    roll_centre_rear: float = 0.0690
    roll_gradient: float = 0.65          # deg/g; used if use_computed_roll_gradient is False
    use_computed_roll_gradient: bool = True   # True: roll gradient from the rates calculator (eq 5-10)
    ackermann_ratio: float = 1.0         # outer / inner steer angle
    motion_ratio_front: float = 1.08     # wheel / spring
    motion_ratio_rear: float = 1.06
    spring_front_lbs_in: float = 350.0   # lbs/inch per corner
    spring_rear_lbs_in: float = 150.0
    tire_stiffness_front: float = 95e3   # N/m per corner
    tire_stiffness_rear: float = 95e3
    arb_front: float = 36450.0           # N/m
    arb_rear: float = 115710.0
    motion_ratio_arb_front: float = 1.07
    motion_ratio_arb_rear: float = 1.56
    # dampers (Table 2-4)
    knee_speed_mm_s: float = 12.0
    zeta_front_ls: float = 4.0           # damping ratio, low speed
    zeta_rear_ls: float = 3.2
    comp_reb_ls: float = 1.5             # compression / rebound ratio, low speed
    zeta_front_hs: float = 0.9           # damping ratio, high speed
    zeta_rear_hs: float = 0.8
    comp_reb_hs: float = 0.5
    # anti features, % (Table 7-1)
    anti_dive: float = 30.3              # front, braking
    anti_lift_rear: float = 13.5         # rear, braking
    anti_lift_front: float = 0.0         # front, acceleration
    anti_squat: float = 20.0             # rear, acceleration


@dataclass
class Brakes:
    """Braking system (thesis Table 2-8)."""
    bias_front: float = 60.0             # % front
    disc_diameter_front: float = 0.185   # m
    disc_diameter_rear: float = 0.174
    pad_height_front: float = 0.04
    pad_height_rear: float = 0.04
    pad_mu_front: float = 0.45
    pad_mu_rear: float = 0.45
    pistons_front: int = 4
    pistons_rear: int = 2
    piston_diameter_front: float = 0.025
    piston_diameter_rear: float = 0.025


@dataclass
class Correlation:
    """Correction factors that make the sim match the real car (thesis Tables 4-8 and 4-14). 1 = no correction."""
    engine_power: float = 1.0     # multiplies engine torque
    aero: float = 1.0             # multiplies CzT and Cx
    mux_accel: float = 1.0        # multiplies longitudinal grip while accelerating
    mux_brake: float = 1.0        # multiplies longitudinal grip while braking
    muy: float = 1.0              # multiplies lateral grip
    load_sensitivity: float = 1.0 # multiplies the tire load sensitivity (simple model)


@dataclass
class Effects:
    """Switches for the physical effects and the simplifying assumptions of the thesis. True = the effect is modelled.

    The defaults reproduce the thesis model exactly. Turn an effect off to see how much it is worth, or to run the
    simpler model the thesis describes. Change them one at a time with `effects_study`.

    Aero (the first four only matter when aero_mode is "sensitivity" or "map"; thesis 6.3, 7.2, 7.3, 8.3.3)
        aero_ride_height   : the ride heights move with downforce and weight transfer. False = static ride height.
        aero_anti_features : anti-dive / lift / squat take part of the weight transfer out of the springs (7.2.1). False =
                             100 % of it goes through the heave springs (the simplified ride height of chapter 4).
        aero_roll          : body roll changes the aero. False = roll is taken as zero.
        aero_yaw           : chassis side slip (yaw) changes the aero. False = yaw is taken as zero.
    Aero and resistance (any aero_mode)
        aero_downforce     : the car makes downforce (False = CzT of zero: no downforce, no ride height change)
        aero_drag          : the car makes aero drag
        rolling_resistance : tires have rolling resistance
    Loads
        longitudinal_weight_transfer : load moves between the axles under acceleration and braking (3.2.2, 3.3.2).
        lateral_weight_transfer      : load moves between the left and right tires in a corner, using the roll centres and
                                       roll stiffness (8.3.2.3). Off in the thesis lap simulation (it only does the
                                       longitudinal part, 2.8); on, load sensitivity then costs grip in every corner.
    Tires
        tire_load_sensitivity : grip changes with tire load. False = constant mu: the measured value for the simple
                                model, the value at the average static tire load (mass g / 4) for Pacejka.
        tire_camber           : the static camber changes the Pacejka grip. False = zero camber.
    The apex drag correction (4.3.1.2) has no switch: without it the car cannot hold the apex speed against drag and the
    lap solver breaks down (lap times come out far too fast).
    """
    aero_ride_height: bool = True
    aero_anti_features: bool = True
    aero_roll: bool = True
    aero_yaw: bool = True
    aero_downforce: bool = True
    aero_drag: bool = True
    rolling_resistance: bool = True
    longitudinal_weight_transfer: bool = True
    lateral_weight_transfer: bool = False
    tire_load_sensitivity: bool = True
    tire_camber: bool = True


def _from_settings(cls, section, name, path):
    """Build a dataclass from one settings.json section. Names starting with _ are notes. A name that is not a
    field is an error (probably a typo); lists become tuples; missing fields keep their default."""
    section = {k: v for k, v in section.items() if not k.startswith("_")}
    unknown = set(section) - set(cls.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown {name} settings {sorted(unknown)}; valid names: {list(cls.__dataclass_fields__)}")
    return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in section.items()})


def load_effects(path=HERE / "settings.json"):
    """Read the "effects" section of settings.json into an Effects. A value that is not true / false is an error."""
    with open(path, encoding="utf8") as f:
        section = json.load(f).get("effects", {})
    bad = {k: v for k, v in section.items() if not k.startswith("_") and not isinstance(v, bool)}
    if bad:
        raise ValueError(f"{path}: effects must be true or false, got {bad}")
    return _from_settings(Effects, section, "effects", path)


def load_car(path=HERE / "settings.json"):
    """Build the whole Car from settings.json: sections "tire", "suspension", "brakes", "correlation", "effects" and "car".

    In "car", aero_map is "placeholder" (the made-up AeroMap), the path of a CSV for AeroMap.from_csv, or null.
    """
    with open(path, encoding="utf8") as f:
        settings = json.load(f)
    unknown = {k for k in settings if not k.startswith("_")} - {"tire", "suspension", "brakes", "correlation", "effects", "car"}
    if unknown:
        raise ValueError(f"{path}: unknown sections {sorted(unknown)}")
    car = {k: v for k, v in settings.get("car", {}).items() if not k.startswith("_")}
    for sub in ("tire", "suspension", "brakes", "correlation"):
        if sub in car:
            raise ValueError(f"{path}: put {sub} settings in the \"{sub}\" section, not in \"car\"")
    aero_map = car.pop("aero_map", None)
    if aero_map == "placeholder":
        aero_map = AeroMap.placeholder()
    elif isinstance(aero_map, str):
        aero_map = AeroMap.from_csv(Path(path).parent / aero_map)
    elif aero_map is not None:
        raise ValueError(f'{path}: aero_map must be "placeholder", a CSV path or null')
    return _from_settings(Car, {
        **car, "aero_map": aero_map,
        "tire": _from_settings(Tire, settings.get("tire", {}), "tire", path),
        "suspension": _from_settings(Suspension, settings.get("suspension", {}), "suspension", path),
        "brakes": _from_settings(Brakes, settings.get("brakes", {}), "brakes", path),
        "correlation": _from_settings(Correlation, settings.get("correlation", {}), "correlation", path),
        "effects": load_effects(path)}, "car", path)


@dataclass
class Car:
    """All car parameters. Defaults = thesis baseline car (Tables 2-1, 2-2, 2-5). Replace with FK09 data."""
    # --- general (mass includes the driver), Table 2-1 ---
    mass: float = 250.0
    weight_dist_front: float = 49.0      # %
    weight_dist_left: float = 50.0       # %
    wheelbase: float = 1.53
    front_track: float = 1.238
    rear_track: float = 1.15
    cog_height: float = 0.330
    drive: str = "RWD"                   # "RWD", "FWD" or "AWD"
    steer_ratio: float = 3.74            # steering wheel / front wheels
    mass_nsm_front: float = 20.0         # non-suspended mass, front axle
    mass_nsm_rear: float = 22.0
    cog_nsm_front: float = 0.26
    cog_nsm_rear: float = 0.28
    cog_sm: float = 0.324                # suspended mass CoG height
    izz: float = 80.0                    # kg m^2
    ixx: float = 15.0
    iyy: float = 60.0
    pitch_centre_x: float = 0.931        # front axle to static pitch centre, m
    # --- aero, Table 2-2 (drag and downforce are positive numbers) ---
    aero_mode: str = "constant"          # "constant", "sensitivity" or "map" (needs aero_map)
    cz_total: float = 4.5
    cx_total: float = 1.75
    frontal_area: float = 1.0
    aero_balance_front: float = 45.0     # %
    air_density: float = 1.225
    static_rh_front: float = 35.0        # mm
    static_rh_rear: float = 45.0
    aero_sens_roll: float = -5.0         # % CzT per degree of roll ("sensitivity" mode)
    aero_sens_yaw: float = -0.8          # % CzT per degree of yaw
    aero_map: object = None              # AeroMap (see section 12)
    rh_limit_mm: float = 5.0             # ride height below this is flagged as bottoming
    # --- engine / drivetrain, Table 2-5. The dyno curve is a PLACEHOLDER ---
    engine_rpm: tuple = (4500, 5500, 6500, 7500, 8500, 9500, 10500)
    engine_torque: tuple = (46, 50, 52, 55, 56, 54, 48)   # Nm
    engine_scale: float = 1.0
    rpm_idle: float = 2000.0
    thermal_efficiency: float = 0.30
    fuel_heating_value: float = 4.72e7   # J/kg
    drivetrain_efficiency: float = 0.90
    primary_ratio: float = 1.0
    final_ratio: float = 3.6
    gear_ratios: tuple = (4.51, 3.23, 2.49, 2.03, 1.71, 1.49)
    # --- sub-models ---
    tire: Tire = field(default_factory=Tire)
    suspension: Suspension = field(default_factory=Suspension)
    brakes: Brakes = field(default_factory=Brakes)
    correlation: Correlation = field(default_factory=Correlation)
    effects: Effects = field(default_factory=Effects)

    def __post_init__(self):
        wd = self.weight_dist_front / 100
        self.a_dist = (1 - wd) * self.wheelbase       # CoG to front axle (eq 2-1)
        self.b_dist = wd * self.wheelbase             # CoG to rear axle (positive here, eq 2-2 is negative)
        self.pitch_arm = abs(self.a_dist - self.pitch_centre_x)
        self.mass_sm = self.mass - self.mass_nsm_front - self.mass_nsm_rear
        self.mass_sm_front = self.mass_sm * wd
        self.mass_sm_rear = self.mass_sm - self.mass_sm_front
        self.rates = suspension_rates(self)           # section 11
        self._lat_wt = tuple(float(w) for w in lateral_weight_transfer(self, 1.0)[:2])   # N per m/s^2 of ay, front / rear axle (linear in ay)
        self._aero_tab = None                         # filled in by _build_aero_table for the "sensitivity" and "map" modes
        self._aero_const = (self.cz_total * self.correlation.aero, self.aero_balance_front, self.cx_total * self.correlation.aero)
        self._build_tire_tables()
        self._build_aero_table()                      # section 2
        self._build_tractive_force()                  # section 3
        self._build_corner_tables()                   # section 7

    def camber(self, axle):
        """Camber [deg] used in the tire model: the static camber, or zero if effects.tire_camber is off."""
        if not self.effects.tire_camber:
            return 0.0
        return self.suspension.camber_front if axle == "front" else self.suspension.camber_rear

    # ---- tire friction tables (peak lateral mu vs load on one tire) ----
    def _build_tire_tables(self):
        self._fz_tire = np.linspace(50.0, 6000.0, 120)
        t = self.tire
        if t.lateral_model == "pacejka":
            self._muy_front = pacejka_peak_mu(t.a, self._fz_tire, self.camber("front"))
            self._muy_rear = pacejka_peak_mu(t.a, self._fz_tire, self.camber("rear"))
        # cornering stiffness of each axle, N/deg (for the steering model, eq 2-53)
        self.cf = t.cornering_stiffness_front
        self.cr = t.cornering_stiffness_rear
        if self.cf is None or self.cr is None:
            fz_f, fz_r = vertical_loads(self, 0.0)
            bcd = lambda fz: t.a[3] * math.sin(math.atan(fz / 1000 / t.a[4]) * 2)
            self.cf = 2 * bcd(fz_f / 2) if self.cf is None else self.cf
            self.cr = 2 * bcd(fz_r / 2) if self.cr is None else self.cr

    # ---- engine force vs speed (thesis 2.6.3, 2.6.4) ----
    def _build_tractive_force(self):
        rpm = np.array(self.engine_rpm, dtype=float)
        torque = np.array(self.engine_torque, dtype=float) * self.engine_scale * self.correlation.engine_power
        self.total_ratio = np.array(self.gear_ratios) * self.primary_ratio * self.final_ratio   # eq 2-21
        r = self.tire.radius
        # speed and wheel force of every gear at every rpm point (eq 2-22 .. 2-25)
        self.gear_v = np.outer(1 / self.total_ratio, rpm * 2 * math.pi / 60 * r)
        self.gear_f = np.outer(self.total_ratio, torque) * self.drivetrain_efficiency / r
        self.v_grid = np.arange(0.0, self.gear_v.max() + 0.1, 0.1)
        forces = np.zeros((len(self.total_ratio), len(self.v_grid)))
        for g in range(len(self.total_ratio)):
            # below the first rpm point the clutch slips: keep the first force value
            forces[g] = np.interp(self.v_grid, self.gear_v[g], self.gear_f[g], left=self.gear_f[g][0], right=0.0)
        self.gear_force_grid = forces
        gear = np.argmax(forces, axis=0)
        gear = np.maximum.accumulate(gear)               # remove false downshifts from a bumpy dyno curve
        self.gear_grid = gear
        self.f_engine_grid = forces[gear, np.arange(len(self.v_grid))]
        self.rpm_grid = self.v_grid / r * self.total_ratio[gear] * 60 / (2 * math.pi)
        # top speed: where engine force equals drag + rolling resistance (straight line, no weight transfer)
        resist = np.array([resistance_force(self, v) for v in self.v_grid])
        above = np.nonzero(self.f_engine_grid > resist)[0]
        self.top_speed = float(self.v_grid[above[-1]]) if len(above) else float(self.v_grid[-1])


# =====================================================================================
# 2. AERO (thesis 2.3, chapters 6 and 7)
#    constant     : CzT, AB, Cx are constants (2.3)
#    sensitivity  : constant values reduced by a %/deg of roll and yaw (used by the yaw moment diagram, 8.3.3)
#    map          : AeroMap that depends on front/rear ride height, roll and yaw (6.2)
# =====================================================================================

class AeroMap:
    """Aero map source data (thesis Table 6-1), from CFD, wind tunnel or track testing.

    data is a table with one row per run and the columns
        frh, rrh : front / rear ride height [mm]
        roll, yaw: roll / yaw angle [deg]
        czt      : total downforce coefficient
        ab       : aero balance [% front]
        cx       : drag coefficient
    Ride heights are swept with roll = yaw = 0 (the main map). Roll and yaw are swept separately at one
    ride height (the "base" ride height) and stored as a loss relative to zero angle (6.2.2).
    """

    def __init__(self, data):
        d = pd.DataFrame(data).copy()
        main = d[(d.roll == 0) & (d.yaw == 0)]
        self.frh = np.sort(main.frh.unique())
        self.rrh = np.sort(main.rrh.unique())
        grid = lambda col: main.pivot_table(index="frh", columns="rrh", values=col).reindex(index=self.frh, columns=self.rrh).values
        self.czt, self.ab, self.cx = grid("czt"), grid("ab"), grid("cx")
        if np.isnan(self.czt).any():
            raise ValueError("The main aero map (roll = 0, yaw = 0) must cover a full front x rear ride height grid.")
        # sensitivities: the rows with roll or yaw only, all at one base ride height
        angle_rows = d[(d.roll != 0) | (d.yaw != 0)]
        if len(angle_rows):
            self.base_frh, self.base_rrh = angle_rows.frh.iloc[0], angle_rows.rrh.iloc[0]
        else:
            self.base_frh, self.base_rrh = float(np.median(self.frh)), float(np.median(self.rrh))
        z = self.main(self.base_frh, self.base_rrh)
        self.roll_sens = self._sensitivity(angle_rows[angle_rows.yaw == 0], "roll", z)
        self.yaw_sens = self._sensitivity(angle_rows[angle_rows.roll == 0], "yaw", z)

    @staticmethod
    def _sensitivity(rows, column, z0):
        """Angles and the change of (CzT %, AB points, Cx %) relative to zero angle."""
        rows = rows.sort_values(column)
        angles = np.concatenate([[0.0], rows[column].values])
        dcz = np.concatenate([[0.0], (rows.czt.values / z0[0] - 1) * 100])
        dab = np.concatenate([[0.0], rows.ab.values - z0[1]])
        dcx = np.concatenate([[0.0], (rows.cx.values / z0[2] - 1) * 100])
        return angles, dcz, dab, dcx

    @staticmethod
    def _bilinear(xs, ys, table, x, y):
        """Bilinear interpolation on a grid; x and y outside the grid use the edge value (6.2.3.2)."""
        x = np.clip(x, xs[0], xs[-1])
        y = np.clip(y, ys[0], ys[-1])
        i = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
        j = np.clip(np.searchsorted(ys, y) - 1, 0, len(ys) - 2)
        u = (x - xs[i]) / (xs[i + 1] - xs[i])
        w = (y - ys[j]) / (ys[j + 1] - ys[j])
        return ((1 - u) * (1 - w) * table[i, j] + u * (1 - w) * table[i + 1, j]
                + (1 - u) * w * table[i, j + 1] + u * w * table[i + 1, j + 1])

    def main(self, frh, rrh):
        """(CzT, AB, Cx) from the main map at ride heights frh, rrh [mm]."""
        return (self._bilinear(self.frh, self.rrh, self.czt, frh, rrh),
                self._bilinear(self.frh, self.rrh, self.ab, frh, rrh),
                self._bilinear(self.frh, self.rrh, self.cx, frh, rrh))

    def coefficients(self, frh, rrh, roll=0.0, yaw=0.0):
        """(CzT, AB, Cx) for the full car state: ride heights, then the roll and yaw sensitivities (Figure 6-14)."""
        cz, ab, cx = self.main(frh, rrh)
        for sens, angle in ((self.roll_sens, roll), (self.yaw_sens, yaw)):
            angles, dcz, dab, dcx = sens
            a = np.clip(np.abs(angle), angles[0], angles[-1])      # beyond the map the edge value is used
            cz = cz * (1 + np.interp(a, angles, dcz) / 100)
            ab = ab + np.interp(a, angles, dab)
            cx = cx * (1 + np.interp(a, angles, dcx) / 100)
        return cz, ab, cx

    @classmethod
    def from_csv(cls, path):
        """Read a table with the columns frh, rrh, roll, yaw, czt, ab, cx."""
        return cls(pd.read_csv(path))

    @classmethod
    def placeholder(cls):
        """A made-up aero map with the shape described in the thesis. PLACEHOLDER: replace with your CFD data."""
        rows = []
        for f in range(5, 50, 5):
            for r in range(5, 50, 5):
                gf = math.exp(-((f - 30) / 18) ** 2)
                gr = math.exp(-((r - 32) / 16) ** 2) if r < 32 else math.exp(-((r - 32) / 30) ** 2)
                czt = 2.0 + 3.5 * gf * gr
                ab = float(np.clip(50 - 0.4 * (f - 30) + 0.25 * (r - 30), 30, 60))
                cx = 1.75 + 0.004 * (r - 30) - 0.002 * (f - 30)
                rows.append((f, r, 0.0, 0.0, czt, ab, cx))
        main = pd.DataFrame(rows, columns=["frh", "rrh", "roll", "yaw", "czt", "ab", "cx"])
        z0 = AeroMap(main).main(30, 35)   # ride height at which roll and yaw are swept
        extra = []
        for roll, dcz, dab in ((0.5, -10.7, 3.6), (1.0, -11.9, 1.5), (1.5, -14.5, 1.6)):
            extra.append((30, 35, roll, 0.0, z0[0] * (1 + dcz / 100), z0[1] + dab, z0[2]))
        for yaw, dcz, dab in ((5, -9.5, 4.9), (10, -19.8, 8.8), (15, -31.0, 8.9), (20, -38.5, 6.0)):
            extra.append((30, 35, 0.0, yaw, z0[0] * (1 + dcz / 100), z0[1] + dab, z0[2]))
        extra = pd.DataFrame(extra, columns=main.columns)
        return cls(pd.concat([main, extra], ignore_index=True))


def steering_angles(car, ay):
    """Steer angle delta and chassis side slip beta [deg] for a lateral acceleration ay [m/s^2] (eq 2-53).

    Bicycle model, neutral steer (yaw moment = 0). Solves
        [ CF          CR - CF           ] [delta]   [ M ay ]
        [ aDist*CF    bDist*CR - aDist*CF ] [beta ] = [  0   ]      with bDist negative as in the thesis.
    """
    beta = car.mass * ay * car.a_dist / (car.cr * car.wheelbase)
    delta = (car.a_dist * car.cf + car.b_dist * car.cr) * beta / (car.a_dist * car.cf)
    return delta, beta


def roll_angle(car, ay):
    """Body roll angle [deg] from the roll gradient (eq 7-10); ay in m/s^2."""
    s = car.suspension
    gradient = car.rates["roll_gradient"] if s.use_computed_roll_gradient else s.roll_gradient
    return gradient * ay / G


def _anti_features(car, ax):
    """Front and rear anti feature (as a fraction) for the sign of the longitudinal acceleration (Table 7-1)."""
    s = car.suspension
    braking = np.asarray(ax) < 0
    front = np.where(braking, s.anti_dive, s.anti_lift_front) / 100
    rear = np.where(braking, s.anti_lift_rear, s.anti_squat) / 100
    return front, rear


def _aero_state_arrays(car, v, ax, ay, iterations=100, yaw=None):
    """Aero coefficients and ride heights for arrays of speed, ax and ay (Figures 6-15, 7-6, 7-7).

    Successive substitution: ride height -> aero map -> downforce -> ride height, until the ride heights stop
    changing. Ride height = static - downforce / heave stiffness - weight transfer through the springs / heave
    stiffness, where only the share (1 - anti) of the weight transfer goes through the springs (eq 7-8, 7-9).
    The switches in car.effects turn the ride height, anti feature, roll and yaw effects off.
    Returns cz, ab, cx, frh, rrh, bottoming, over_map (all arrays).
    """
    v, ax, ay = np.broadcast_arrays(np.asarray(v, float), np.asarray(ax, float), np.asarray(ay, float))
    corr = car.correlation.aero
    fx = car.effects
    roll = roll_angle(car, np.abs(ay))
    if yaw is None:
        yaw = steering_angles(car, np.abs(ay))[1]       # chassis side slip from the steering model
    yaw = np.abs(yaw)
    if not fx.aero_roll:
        roll = np.zeros_like(roll)
    if not fx.aero_yaw:
        yaw = np.zeros(v.shape)
    kh_f = car.rates["heave_stiffness_front"] / 1000       # N/mm
    kh_r = car.rates["heave_stiffness_rear"] / 1000
    anti_f, anti_r = _anti_features(car, ax)
    if not fx.aero_anti_features:
        anti_f, anti_r = 0.0 * anti_f, 0.0 * anti_r
    wt = weight_transfer(car, ax)                                      # N, positive moves load to the rear
    q = 0.5 * car.air_density * car.frontal_area * v * v
    frh = np.full(v.shape, car.static_rh_front)
    rrh = np.full(v.shape, car.static_rh_rear)
    amap = car.aero_map
    lo_f, hi_f = (amap.frh[0], amap.frh[-1]) if amap else (-np.inf, np.inf)
    lo_r, hi_r = (amap.rrh[0], amap.rrh[-1]) if amap else (-np.inf, np.inf)
    for _ in range(iterations):
        if car.aero_mode == "map":
            cz, ab, cx = amap.coefficients(frh, rrh, roll, yaw)
        else:   # "sensitivity"
            cz = car.cz_total * (1 + car.aero_sens_roll * roll / 100) * (1 + car.aero_sens_yaw * yaw / 100)
            cx = car.cx_total * (1 + car.aero_sens_roll * roll / 100) * (1 + car.aero_sens_yaw * yaw / 100)
            ab = np.full(v.shape, car.aero_balance_front)
        cz, cx = cz * corr, cx * corr
        df = q * cz if fx.aero_downforce else 0.0 * q
        new_frh = car.static_rh_front - (ab / 100 * df / 2 + (-wt) * (1 - anti_f) / 2) / kh_f
        new_rrh = car.static_rh_rear - ((1 - ab / 100) * df / 2 + wt * (1 - anti_r) / 2) / kh_r
        if not fx.aero_ride_height:                    # the suspension does not move the car: static ride height
            new_frh = np.full(v.shape, float(car.static_rh_front))
            new_rrh = np.full(v.shape, float(car.static_rh_rear))
        new_frh_c = np.clip(new_frh, lo_f, hi_f)
        new_rrh_c = np.clip(new_rrh, lo_r, hi_r)
        done = np.max(np.abs(new_frh_c - frh)) < 1e-4 and np.max(np.abs(new_rrh_c - rrh)) < 1e-4
        frh = frh + 0.7 * (new_frh_c - frh)
        rrh = rrh + 0.7 * (new_rrh_c - rrh)
        if done:
            break
    bottoming = (new_frh < max(car.rh_limit_mm, lo_f)) | (new_rrh < max(car.rh_limit_mm, lo_r))
    over_map = (new_frh > hi_f) | (new_rrh > hi_r)
    return cz, ab, cx, frh, rrh, bottoming, over_map


def _build_aero_table(self):
    """Pre-compute the converged aero state on a (speed, ax, ay) grid so the solvers can interpolate it."""
    corr = self.correlation.aero
    self._aero_const = (self.cz_total * corr, self.aero_balance_front, self.cx_total * corr)
    self._aero_tab = None
    if self.aero_mode == "constant":
        return
    if self.aero_mode == "map" and self.aero_map is None:
        raise ValueError('aero_mode = "map" needs aero_map (for example AeroMap.placeholder())')
    v_max = np.array(self.engine_rpm).max() * 2 * math.pi / 60 * self.tire.radius / (self.gear_ratios[-1] * self.primary_ratio * self.final_ratio)
    self._aero_v = np.arange(0.0, v_max * 1.05 + 1.0, 1.0)
    self._aero_ax = np.arange(-4.0, 3.01, 0.5) * G
    self._aero_ay = np.arange(0.0, 3.21, 0.4) * G
    V, AX, AY = np.meshgrid(self._aero_v, self._aero_ax, self._aero_ay, indexing="ij")
    out = _aero_state_arrays(self, V, AX, AY)
    self._aero_tab = np.stack(out[:5], axis=-1)           # cz, ab, cx, frh, rrh
    self._aero_flags = np.stack([out[5], out[6]], axis=-1).astype(float)


def _lookup_aero(car, v, ax, ay, table="_aero_tab"):
    """Trilinear interpolation of the aero state table. Outside the grid the edge is used."""
    tab = getattr(car, table)
    fv = min(max(v, 0.0), car._aero_v[-1] - 1e-9)
    fa = min(max(ax, car._aero_ax[0]), car._aero_ax[-1] - 1e-9)
    fy = min(max(abs(ay), 0.0), car._aero_ay[-1] - 1e-9)
    gv = fv / 1.0
    ga = (fa - car._aero_ax[0]) / (0.5 * G)
    gy = fy / (0.4 * G)
    i, j, k = int(gv), int(ga), int(gy)
    u, w, z = gv - i, ga - j, gy - k
    c = tab[i:i + 2, j:j + 2, k:k + 2]
    return np.einsum("i,j,k,ijkl->l", (1 - u, u), (1 - w, w), (1 - z, z), c)


def aero_coeffs(car, v, ax=0.0, ay=0.0):
    """(CzT, aero balance % front, Cx) at speed v, longitudinal acceleration ax and lateral acceleration ay [m/s^2]."""
    if car._aero_tab is None:
        return car._aero_const
    s = _lookup_aero(car, v, ax, ay)
    return s[0], s[1], s[2]


def aero_state(car, v, ax=0.0, ay=0.0):
    """All aero state values at one point: CzT, AB, Cx, front and rear ride height [mm], flags."""
    if car._aero_tab is None:
        return {"cz": car._aero_const[0], "ab": car._aero_const[1], "cx": car._aero_const[2],
                "frh": np.nan, "rrh": np.nan, "bottoming": False, "over_map": False}
    s = _lookup_aero(car, v, ax, ay)
    f = _lookup_aero(car, v, ax, ay, "_aero_flags")
    return {"cz": s[0], "ab": s[1], "cx": s[2], "frh": s[3], "rrh": s[4], "bottoming": f[0] > 0.5, "over_map": f[1] > 0.5}


# =====================================================================================
# 3. FORCES MODEL (thesis 2.4 - 2.8)
# =====================================================================================

Car._build_aero_table = _build_aero_table


def downforce(car, v, aero=None):
    """Front and rear aero downforce [N] (eq 2-29, 2-30)."""
    if not car.effects.aero_downforce:
        return 0.0, 0.0
    cz, ab, _ = aero or aero_coeffs(car, v)
    total = 0.5 * car.air_density * cz * car.frontal_area * v * v
    return total * ab / 100, total * (1 - ab / 100)


def drag_force(car, v, aero=None):
    """Aero drag [N] (eq 2-35)."""
    if not car.effects.aero_drag:
        return 0.0
    cx = (aero or aero_coeffs(car, v))[2]
    return 0.5 * car.air_density * cx * car.frontal_area * v * v


def rolling_force(car, v, aero=None):
    """Rolling resistance [N] (eq 2-36)."""
    if not car.effects.rolling_resistance:
        return 0.0
    df_front, df_rear = downforce(car, v, aero)
    return car.tire.rolling_resistance * (car.mass * G + df_front + df_rear)


def resistance_force(car, v, aero=None):
    """Drag plus rolling resistance [N] (eq 2-45)."""
    aero = aero or aero_coeffs(car, v)
    return drag_force(car, v, aero) + rolling_force(car, v, aero)


def weight_transfer(car, ax):
    """Longitudinal weight transfer [N] (eq 3-8). Positive ax moves load to the rear axle."""
    if not car.effects.longitudinal_weight_transfer:
        return 0.0 * ax
    return car.cog_height * car.mass * ax / car.wheelbase


def vertical_loads(car, v, ax=0.0, aero=None):
    """Vertical load on the front and rear axle [N], with longitudinal weight transfer (eq 2-26 .. 2-33)."""
    wt = weight_transfer(car, ax)
    df_front, df_rear = downforce(car, v, aero)
    total = car.mass * G + df_front + df_rear
    front = car.mass * G * car.weight_dist_front / 100 - wt + df_front
    # an axle cannot carry less than zero: the other axle then carries the whole load (wheelie / nose lift limit)
    front = min(max(front, 1.0), total - 1.0)
    return front, total - front


def tire_mu(car, fz_axle, axle, direction, braking=False):
    """Peak friction coefficient of the tires on one axle, with load sensitivity and correlation factors.

    axle is "front" or "rear"; direction is "x" (longitudinal) or "y" (lateral) (eq 2-12 .. 2-15).
    """
    t, corr = car.tire, car.correlation
    sensitive = car.effects.tire_load_sensitivity
    fz_tire = fz_axle / 2
    if direction == "x":
        mu = t.mux + (t.mux_sens * corr.load_sensitivity * (t.mux_norm_kg * G - fz_tire) if sensitive else 0.0)
        mu *= corr.mux_brake if braking else corr.mux_accel
    elif t.lateral_model == "pacejka":
        table = car._muy_front if axle == "front" else car._muy_rear
        load = fz_tire if sensitive else car.mass * G / 4       # constant mu: read the table at the average static load
        mu = float(np.interp(load, car._fz_tire, table)) * corr.muy
    else:
        mu = (t.muy + (t.muy_sens * corr.load_sensitivity * (t.muy_norm_kg * G - fz_tire) if sensitive else 0.0)) * corr.muy
    return max(mu, 0.05)


def tire_accel_limit(car, v, ax=0.0, aero=None):
    """Largest forward force the tires of the driven axle(s) can give [N] (eq 2-37 .. 2-40)."""
    fz_front, fz_rear = vertical_loads(car, v, ax, aero)
    force = 0.0
    if car.drive in ("FWD", "AWD"):
        force += tire_mu(car, fz_front, "front", "x") * fz_front
    if car.drive in ("RWD", "AWD"):
        force += tire_mu(car, fz_rear, "rear", "x") * fz_rear
    return force


def tire_brake_limit(car, v, ax=0.0, aero=None):
    """Largest braking force, all four tires [N] (eq 2-41 .. 2-43)."""
    fz_front, fz_rear = vertical_loads(car, v, ax, aero)
    return (tire_mu(car, fz_front, "front", "x", True) * fz_front + tire_mu(car, fz_rear, "rear", "x", True) * fz_rear)


def _lateral_capacity(car, fz_front, fz_rear, ay=None):
    """Largest lateral force of the front and the rear axle [N] for axle loads fz_front, fz_rear.

    With effects.lateral_weight_transfer and a lateral acceleration ay [m/s^2] the load moves from the inner to the
    outer tire of each axle (8.3.2.3) and the two tires are added up, so load sensitivity costs grip. Without it
    the load is shared equally between the two tires of an axle (eq 2-46 .. 2-48).
    """
    if ay is None or not car.effects.lateral_weight_transfer:
        return tire_mu(car, fz_front, "front", "y") * fz_front, tire_mu(car, fz_rear, "rear", "y") * fz_rear
    out = []
    for axle, fz, per_ay in (("front", fz_front, car._lat_wt[0]), ("rear", fz_rear, car._lat_wt[1])):
        shift = min(per_ay * abs(ay), fz / 2 - 10.0)               # the inner tire keeps at least 10 N
        out.append(sum(tire_mu(car, 2 * f, axle, "y") * f for f in (fz / 2 + shift, fz / 2 - shift)))
    return out[0], out[1]


def tire_lateral_limit(car, v, aero=None, ay=None):
    """Largest lateral force of all four tires [N] (eq 2-46 .. 2-48). Give ay [m/s^2] to include the lateral
    weight transfer when effects.lateral_weight_transfer is on."""
    fz_front, fz_rear = vertical_loads(car, v, 0.0, aero)
    return sum(_lateral_capacity(car, fz_front, fz_rear, ay))


def tire_axle_forces(car, v, ax=0.0, ay=0.0, aero=None):
    """Lateral and longitudinal tire force on each axle [N] for the driven channels (FyF, FyR, FxF, FxR)."""
    fz_front, fz_rear = vertical_loads(car, v, ax, aero)
    cap_f, cap_r = _lateral_capacity(car, fz_front, fz_rear, ay)
    # the total lateral force m*ay is shared in proportion to each axle's lateral capacity (neutral steer)
    fy_total = car.mass * abs(ay)
    share_f = cap_f / (cap_f + cap_r)
    fy_f, fy_r = fy_total * share_f, fy_total * (1 - share_f)
    fx_total = car.mass * ax + resistance_force(car, v, aero)       # force the tires must provide
    if ax >= 0:
        drive = {"RWD": (0.0, 1.0), "FWD": (1.0, 0.0), "AWD": (0.5, 0.5)}[car.drive]
        fx_f, fx_r = fx_total * drive[0], fx_total * drive[1]
    else:
        bias = car.brakes.bias_front / 100
        fx_f, fx_r = fx_total * bias, fx_total * (1 - bias)
    return fy_f, fy_r, fx_f, fx_r


def engine_force(car, v):
    """Tractive force at the wheels [N] in the best gear at speed v."""
    return float(np.interp(v, car.v_grid, car.f_engine_grid))


def gear_and_rpm(car, v):
    """Selected gear (1 = first) and engine speed [rpm] at speed v."""
    i = min(int(max(v, 0.0) / 0.1), len(car.v_grid) - 1)
    return int(car.gear_grid[i]) + 1, max(float(np.interp(v, car.v_grid, car.rpm_grid)), car.rpm_idle)


# =====================================================================================
# 4. SHIFTING MODEL (thesis 2.7)
#    No shift delay, no clutch delay, no throttle lift.
# =====================================================================================

def shift_table(car):
    """Gear matrix (Table 2-7): speed and engine speed right before and after every gear change."""
    g = car.gear_grid
    rows = []
    for i in np.nonzero(np.diff(g))[0]:
        rows.append({"shift": f"{g[i] + 1}-->{g[i + 1] + 1}",
                     "vCar before [km/h]": car.v_grid[i] * 3.6, "vCar after [km/h]": car.v_grid[i + 1] * 3.6,
                     "rpm before": car.rpm_grid[i], "rpm after": car.rpm_grid[i + 1],
                     "rev drop [rpm]": car.rpm_grid[i] - car.rpm_grid[i + 1]})
    return pd.DataFrame(rows).set_index("shift")


# =====================================================================================
# 5. STEERING, BRAKING AND THROTTLE MODELS (thesis 2.9)
#    "Driven channels": they are calculated from the result, they do not drive the simulation.
# =====================================================================================

def steering_wheel_angle(car, ay):
    """Steering wheel angle [deg] for a lateral acceleration ay [m/s^2] (eq 2-54)."""
    return car.steer_ratio * steering_angles(car, ay)[0]


def brake_pressure(car, ax):
    """Front and rear brake line pressure [bar] for a deceleration ax [m/s^2] (eq 2-55 .. 2-63).

    The brakes are assumed to use all the tire grip without locking, so the pressure is back-calculated.
    """
    b = car.brakes
    force = car.mass * abs(ax)                                             # eq 2-55
    out = []
    for share, d_disc, h_pad, mu_pad, n, d_piston in (
            (b.bias_front / 100, b.disc_diameter_front, b.pad_height_front, b.pad_mu_front, b.pistons_front, b.piston_diameter_front),
            (1 - b.bias_front / 100, b.disc_diameter_rear, b.pad_height_rear, b.pad_mu_rear, b.pistons_rear, b.piston_diameter_rear)):
        per_corner = share * force / 2                                      # eq 2-56
        torque = per_corner * car.tire.radius                               # eq 2-57
        tangential = torque / (d_disc / 2 - h_pad / 2)                      # eq 2-58, 2-59
        clamp = tangential / mu_pad                                         # eq 2-60
        piston_area = n * math.pi * d_piston ** 2 / 4                       # eq 2-61
        out.append(clamp / piston_area / 1e5)                               # eq 2-62, 2-63
    return out[0], out[1]


def throttle_position(car, v, ax, aero=None):
    """Throttle position [%] (eq 2-68): 100 % means the car is limited by engine power, not grip. 0 % when braking."""
    resist = resistance_force(car, v, aero)
    available = engine_force(car, v) - resist
    if available < 1.0:
        return 100.0 if ax > -0.05 * G else 0.0
    return min(max(100.0 * car.mass * ax / available, 0.0), 100.0)


# =====================================================================================
# 6. GG / GGV DIAGRAM (thesis 2.10)
#    The GGV map is the performance envelope: for every speed, the longitudinal acceleration the car can
#    reach for every lateral acceleration, limited by tires, engine and drag.
# =====================================================================================

def ggv_map(car, speeds=None, points=41):
    """GGV map as a table: columns v [m/s], ay, ax_accel, ax_brake [m/s^2] (right turns are mirrored)."""
    if speeds is None:
        speeds = np.linspace(2.0, car.top_speed, 12)
    rows = []
    for v in speeds:
        ay_max = max(pure_lateral_accel(car, v), 1e-3)
        for ay in np.linspace(-ay_max, ay_max, points):
            ax_acc = combined_accel(car, v, max(v * v / max(abs(ay), 1e-6), 1e-3), 0.0, True, ay=abs(ay))
            ax_brk = combined_accel(car, v, max(v * v / max(abs(ay), 1e-6), 1e-3), 0.0, False, ay=abs(ay))
            rows.append((v, ay, ax_acc, ax_brk))
    return pd.DataFrame(rows, columns=["v", "ay", "ax_accel", "ax_brake"])


def plot_ggv(car, speeds=(10.0, 20.0, 30.0)):
    """GG diagram (lateral vs. longitudinal acceleration in g) for a few speeds (Figure 2-40)."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 6))
    for v in speeds:
        m = ggv_map(car, [min(v, car.top_speed)])
        ax.plot(m.ay / G, m.ax_accel / G, label=f"{v * 3.6:.0f} km/h")
        ax.plot(m.ay / G, m.ax_brake / G, color=ax.lines[-1].get_color())
    ax.set(xlabel="lateral acceleration [g]", ylabel="longitudinal acceleration [g]", title="GG diagram", aspect="equal")
    ax.axhline(0, color="gray", lw=0.5)
    ax.axvline(0, color="gray", lw=0.5)
    ax.legend()
    return fig


# =====================================================================================
# 7. CORNER SPEED AND THE FRICTION ELLIPSE (thesis 3.4, 4.3.1)
# =====================================================================================

def pure_lateral_accel(car, v):
    """Largest lateral acceleration at speed v with all tire grip used for cornering [m/s^2] (thesis 3.4).

    The aero state depends on ay (roll, yaw), so this is solved by successive substitution.
    """
    ay = 0.0
    for _ in range(12):
        aero = aero_coeffs(car, v, 0.0, ay)
        new = tire_lateral_limit(car, v, aero, ay) / car.mass
        if abs(new - ay) < 1e-4:
            break
        ay = 0.5 * (ay + new) if ay else new
    return new


def apex_lateral_accel(car, v):
    """Lateral acceleration left for cornering at constant speed v [m/s^2] (thesis 4.3.1.2, Figure 4-43).

    At constant speed the driven tires must cancel drag and rolling resistance, which uses up part of their
    grip. The friction ellipse gives what is left for cornering.
    """
    ay = pure_lateral_accel(car, v)
    for _ in range(12):
        aero = aero_coeffs(car, v, 0.0, ay)
        ay_max = tire_lateral_limit(car, v, aero, ay) / car.mass
        used = min(resistance_force(car, v, aero) / max(tire_accel_limit(car, v, 0.0, aero), 1.0), 1.0)
        new = max(ay_max * math.sqrt(1 - used * used), 1e-3)
        if abs(new - ay) < 1e-4:
            break
        ay = 0.5 * (ay + new)
    return new


def _build_corner_tables(self):
    """Minimum corner radius vs speed (the thesis "minimum radius vector"), pure and with the apex correction."""
    v = np.linspace(1.0, self.top_speed, 150)
    for name, func in (("pure", pure_lateral_accel), ("apex", apex_lateral_accel)):
        ay = np.array([func(self, s) for s in v])
        radius = np.maximum.accumulate(v ** 2 / ay)         # smallest radius the car can hold at each speed
        setattr(self, f"_corner_{name}", (v, radius))


Car._build_corner_tables = _build_corner_tables


def corner_speed(car, radius, apex=True):
    """Steady cornering speed [m/s] for a radius [m] (interpolates the minimum-radius table).

    apex=True includes the friction ellipse correction for drag (used in the LapSim, 4.3.1);
    apex=False is the pure cornering scenario (3.4).
    """
    v, r = car._corner_apex if apex else car._corner_pure
    return np.interp(radius, r, v)


def combined_accel(car, v, radius, ax_prev, accelerating, ay=None, weight_transfer_on=True):
    """Longitudinal acceleration [m/s^2] available while also cornering at radius (thesis 4.3.2, 4.3.3).

    Friction ellipse: (ay/ay_max)^2 + (ax/ax_max)^2 = 1. Returns a positive number when accelerating and a
    negative number when braking. Weight transfer uses the previous step's ax.
    """
    ay = v * v / radius if ay is None else ay
    ax_loads = ax_prev if weight_transfer_on else 0.0
    aero = aero_coeffs(car, v, ax_loads, ay)
    ay_max = tire_lateral_limit(car, v, aero, ay) / car.mass
    ratio = min(ay / ay_max, 1.0)
    ellipse = math.sqrt(1 - ratio * ratio)
    resist = resistance_force(car, v, aero) / car.mass
    if accelerating:
        ax_tire = tire_accel_limit(car, v, ax_loads, aero) * ellipse / car.mass
        ax_engine = engine_force(car, v) / car.mass
        return min(ax_tire, ax_engine) - resist              # eq 4-10
    ax_tire = tire_brake_limit(car, v, ax_loads, aero) * ellipse / car.mass
    return -(ax_tire + resist)                               # drag and rolling resistance help braking


# =====================================================================================
# 8. SPECIFIC SCENARIOS (thesis chapter 3)
#    Acceleration, braking and cornering, each on its own. Weight transfer (3.2.2, 3.3.2) and the aero map
#    (7.4) are included when the car is set up for them.
# =====================================================================================

def driven_channels(car, s, t, v, ax, ay):
    """Back-calculate the "driven channels" from the speed trace (thesis 4.4.1, Table 4-6).

    s, t, v, ax, ay are arrays: distance [m], time [s], speed [m/s], longitudinal and lateral acceleration [m/s^2].
    Returns a DataFrame with one row per point.
    """
    rows = []
    for si, ti, vi, axi, ayi in zip(s, t, v, ax, ay):
        aero_state_i = aero_state(car, vi, axi, ayi)
        aero = (aero_state_i["cz"], aero_state_i["ab"], aero_state_i["cx"])
        resist = resistance_force(car, vi, aero)
        fy_f, fy_r, fx_f, fx_r = tire_axle_forces(car, vi, axi, ayi, aero)
        # braking pressure only for the part of the deceleration that the brakes provide (not drag)
        ax_brake = max(0.0, -axi - resist / car.mass)
        p_f, p_r = brake_pressure(car, ax_brake)
        gear, rpm = gear_and_rpm(car, vi)
        delta, beta = steering_angles(car, ayi)
        drive_force = max(car.mass * axi + resist, 0.0)
        grip_limited = not (axi > 0.05 and engine_force(car, vi) <= tire_accel_limit(car, vi, axi, aero))
        rows.append({
            "s": si, "t": ti, "v": vi, "v_kmh": vi * 3.6, "ax": axi, "ay": ayi,
            "FyF": fy_f, "FyR": fy_r, "FxF": fx_f, "FxR": fx_r,
            "steering [deg]": car.steer_ratio * delta, "pBrakeF [bar]": p_f, "pBrakeR [bar]": p_r,
            "TPS [%]": throttle_position(car, vi, axi, aero), "rpm": rpm, "gear": gear,
            "WF [kg]": weight_transfer(car, axi) / G, "CzT": aero[0], "AB": aero[1], "Cx": aero[2],
            "FRH [mm]": aero_state_i["frh"], "RRH [mm]": aero_state_i["rrh"], "beta [deg]": beta,
            "roll [deg]": roll_angle(car, ayi), "drive force [N]": drive_force, "grip limited": grip_limited,
            "bottoming": aero_state_i["bottoming"], "over aero map": aero_state_i["over_map"],
        })
    return pd.DataFrame(rows)


def _straight_line(car, v0, accelerating, solver, step, distance, speed, time_limit, weight_transfer_on):
    """Solve a straight-line acceleration or braking run (thesis 3.2.1, 3.3.1).

    solver "time": v = v0 + a dt, dx = v0 dt + a dt^2 / 2 (eq 3-2, 3-3)
    solver "distance": v^2 = v0^2 + 2 a dx, dt = (v - v0) / a (eq 3-4, 3-5)
    The run ends at the first target (distance, speed or time) or when the car stops accelerating
    (top speed) or stops (braking).
    """
    t = s = 0.0
    v, a = v0, 0.0
    T, S, V, A = [0.0], [0.0], [v0], []
    first = True
    while True:
        a = combined_accel(car, v, 1e9, a, accelerating, 0.0, weight_transfer_on)
        if first:     # the weight transfer needs an acceleration: refine once at the start
            a = combined_accel(car, v, 1e9, a, accelerating, 0.0, weight_transfer_on)
            first = False
        A.append(a)
        if (accelerating and a <= 1e-6) or (not accelerating and v <= 1e-9):
            break
        if solver == "time":
            dt = step
            ds = v * dt + 0.5 * a * dt * dt
            v_new = v + a * dt
        else:
            ds = step
            disc = v * v + 2 * a * ds
            if disc <= 0:                      # the car stops before the end of this step
                ds = v * v / (-2 * a)
                v_new = 0.0
            else:
                v_new = math.sqrt(disc)
            dt = (v_new - v) / a
        fraction = 1.0
        if distance is not None and s + ds >= distance:
            fraction = min(fraction, (distance - s) / ds)
        if speed is not None and ((accelerating and v_new >= speed) or (not accelerating and v_new <= speed)):
            fraction = min(fraction, (speed - v) / (v_new - v))
        if time_limit is not None and t + dt >= time_limit:
            fraction = min(fraction, (time_limit - t) / dt)
        stopped = not accelerating and v_new <= 1e-9
        if stopped:
            fraction = 1.0
        t, s, v = t + dt * fraction, s + ds * fraction, v + (v_new - v) * fraction
        T.append(t)
        S.append(s)
        V.append(v)
        if fraction < 1.0 or stopped:
            A.append(a)
            break
    return np.array(T), np.array(S), np.array(V), np.array(A)


def run_acceleration(car, solver="distance", step=None, initial_speed=0.0, distance=None, speed=None, time=None,
                     weight_transfer_on=True):
    """Acceleration scenario (thesis 3.2): a standing start (or initial_speed), until `distance`, `speed`, `time`
    or top speed. Default steps are the thesis values: 0.25 m for the distance solver, 0.001 s for the time solver.

    Returns {"time", "distance", "end_speed", "max_speed", "trace"}; trace holds all the channels.
    """
    step = step or (0.25 if solver == "distance" else 0.001)
    if initial_speed > car.top_speed:
        raise ValueError("initial_speed is above the top speed of the car")
    T, S, V, A = _straight_line(car, initial_speed, True, solver, step, distance, speed, time, weight_transfer_on)
    trace = driven_channels(car, S, T, V, A, np.zeros_like(V))
    return {"time": float(T[-1]), "distance": float(S[-1]), "end_speed": float(V[-1]), "max_speed": float(V.max()), "trace": trace}


def run_braking(car, solver="distance", step=None, initial_speed=None, distance=None, speed=0.0, time=None,
                weight_transfer_on=True):
    """Braking scenario (thesis 3.3): from initial_speed (default top speed) down to `speed` (default 0),
    or until `distance` or `time`. Default steps: 0.15 m (distance solver), 0.001 s (time solver)."""
    step = step or (0.15 if solver == "distance" else 0.001)
    v0 = car.top_speed if initial_speed is None else initial_speed
    T, S, V, A = _straight_line(car, v0, False, solver, step, distance, speed, time, weight_transfer_on)
    trace = driven_channels(car, S, T, V, A, np.zeros_like(V))
    return {"time": float(T[-1]), "distance": float(S[-1]), "end_speed": float(V[-1]), "max_speed": float(V.max()), "trace": trace}


def run_cornering(car, radius=9.125, rotation=360.0, distance=None):
    """Steady-state cornering scenario (thesis 3.4). The default is the Formula Student skidpad: 9.125 m, 360 deg.

    The speed follows from the minimum radius table (V = sqrt(ay R), eq 3-19). Time = distance / speed (eq 3-22).
    """
    v = float(corner_speed(car, radius, apex=False))
    dist = distance if distance is not None else 2 * math.pi * radius * rotation / 360        # eq 3-20
    time = dist / v
    ay = v * v / radius
    trace = driven_channels(car, [0.0, dist], [0.0, time], [v, v], [0.0, 0.0], [ay, ay])
    return {"time": time, "distance": dist, "speed": v, "lat_accel": ay / G, "trace": trace}


# =====================================================================================
# 9. TRACK MODEL (thesis 4.2)
#    A track is a list of points with distance, corner radius and direction (left / right).
#    Four ways to make one (4.2.2): radius & length, GPS coordinates, XY coordinates, acceleration & speed.
#    Then optional filtering, a fine mesh in the corners (4.2.3) and apex smoothing (done in the LapSim).
# =====================================================================================

R_STRAIGHT = 1e5      # radius used for a straight section (thesis Table 4-1)


@dataclass
class Track:
    name: str
    s: np.ndarray            # distance of each point along the track [m]
    radius: np.ndarray       # corner radius at each point [m], always positive, R_STRAIGHT on straights
    direction: np.ndarray    # +1 left corner, -1 right corner
    x: np.ndarray            # position [m], for plotting
    y: np.ndarray
    closed: bool             # True for a lap that repeats (endurance), False for a run (autocross)
    dl: np.ndarray           # length of the section from each point to the next [m]

    @property
    def length(self):
        return float(np.sum(self.dl)) if self.closed else float(self.s[-1])

    @property
    def curvature(self):
        """Signed curvature [1/m]: positive for left corners (thesis eq 4-1)."""
        return self.direction / self.radius


def _smooth(a, n, closed):
    """Moving average over n points (wraps around on a closed track)."""
    n = max(int(round(n)), 1)
    if n == 1:
        return np.asarray(a, float)
    kernel = np.ones(n) / n
    a = np.asarray(a, float)
    if closed:
        padded = np.concatenate([a[-n:], a, a[:n]])
    else:
        padded = np.concatenate([np.full(n, a[0]), a, np.full(n, a[-1])])
    return np.convolve(padded, kernel, mode="same")[n:-n]


def moving_average(a, window, closed=False):
    """Moving average filter with a window in samples (thesis 4.2.3.1)."""
    return _smooth(a, window, closed)


def _lfilter(b, a, x):
    """Plain IIR filter y = filter(b, a, x) (direct form I)."""
    y = np.zeros(len(x))
    for n in range(len(x)):
        acc = sum(b[k] * x[n - k] for k in range(len(b)) if n - k >= 0)
        acc -= sum(a[k] * y[n - k] for k in range(1, len(a)) if n - k >= 0)
        y[n] = acc / a[0]
    return y


def butterworth_lowpass(x, cutoff_hz, sample_hz, order=2):
    """Zero-phase low-pass Butterworth filter (thesis 4.2.2.4, 4.2.3.1), forward and backward.

    Built from the analog prototype with the bilinear transform; no extra libraries needed.
    """
    x = np.asarray(x, float)
    fs = float(sample_hz)
    wc = 2 * fs * math.tan(math.pi * cutoff_hz / fs)                     # pre-warped cutoff
    k = np.arange(order)
    poles_s = wc * np.exp(1j * math.pi * (2 * k + order + 1) / (2 * order))
    poles_z = (1 + poles_s / (2 * fs)) / (1 - poles_s / (2 * fs))
    a = np.real(np.poly(poles_z))
    b = np.real(np.poly(-np.ones(order)))
    b = b * (np.sum(a) / np.sum(b))                                       # unit gain at 0 Hz
    pad = min(3 * order * 4, len(x) - 1)
    padded = np.concatenate([2 * x[0] - x[pad:0:-1], x, 2 * x[-1] - x[-2:-pad - 2:-1]])
    forward = _lfilter(b, a, padded)
    backward = _lfilter(b, a, forward[::-1])[::-1]
    return backward[pad:len(backward) - pad]


def _derivative(a, ds, closed):
    if closed:
        return (np.roll(a, -1) - np.roll(a, 1)) / (2 * ds)
    return np.gradient(a, ds)


def _make_track(name, s, curvature, x, y, closed, dl=None):
    curvature = np.asarray(curvature, float)
    radius = np.minimum(1 / np.maximum(np.abs(curvature), 1 / R_STRAIGHT), R_STRAIGHT)
    direction = np.where(curvature >= 0, 1, -1)
    if dl is None:
        dl = np.diff(s, append=s[-1] + (s[-1] - s[-2]) if closed else s[-1])
        if not closed:
            dl[-1] = 0.0
    return Track(name, np.asarray(s, float), radius, direction, np.asarray(x, float), np.asarray(y, float), closed, np.asarray(dl, float))


# ---- option 1: radius & length (4.2.2.1) ----

def xy_from_radius_length(radius, section_length, alpha0=0.0, offset=0.0, moving_average_points=1, closed=False):
    """XY coordinates from signed radius (+ left, - right) and section length (eq 4-2 .. 4-6).

    alpha0 rotates the whole track [rad]; offset [%] scales every turning angle (can open a closed track);
    moving_average_points smooths the result.
    """
    radius = np.asarray(radius, float)
    dl = np.asarray(section_length, float)
    x = np.zeros(len(radius) + 1)
    y = np.zeros(len(radius) + 1)
    alpha = alpha0
    for i, (r, d) in enumerate(zip(radius, dl)):
        dtheta = d / r                                                 # eq 4-2
        along = r * math.sin(dtheta)                                   # eq 4-4: distance along the heading
        side = r * (1 - math.cos(dtheta))                              # distance towards the centre of the corner
        x[i + 1] = x[i] + along * math.cos(alpha) - side * math.sin(alpha)
        y[i + 1] = y[i] + along * math.sin(alpha) + side * math.cos(alpha)
        alpha += dtheta * (1 + offset / 100)                           # eq 4-3
    x, y = x[:-1], y[:-1]
    return _smooth(x, moving_average_points, closed), _smooth(y, moving_average_points, closed)


def track_from_radius_length(radius, section_length, closed=True, name="track", **xy_options):
    """Track from a table of signed corner radius [m] (positive left, negative right, 1e5 for straights) and
    section length [m] (thesis Table 4-1). xy_options go to xy_from_radius_length."""
    radius = np.asarray(radius, float)
    dl = np.asarray(section_length, float)
    s = np.concatenate([[0.0], np.cumsum(dl)[:-1]])
    x, y = xy_from_radius_length(radius, dl, closed=closed, **xy_options)
    return _make_track(name, s, 1 / radius, x, y, closed, dl)


def load_radius_length_csv(path, closed=True, name=None):
    """Read a Radius & Length file with the columns radius, length (and optionally direction: Left/Right/Straight)."""
    d = pd.read_csv(path)
    d.columns = [c.lower() for c in d.columns]
    radius = d[[c for c in d.columns if "radius" in c][0]].values.astype(float)
    length = d[[c for c in d.columns if "length" in c][0]].values.astype(float)
    dir_cols = [c for c in d.columns if "direction" in c]
    if dir_cols:
        sign = d[dir_cols[0]].astype(str).str.lower().map({"left": 1, "right": -1}).fillna(1).values
        radius = np.abs(radius) * sign
    return track_from_radius_length(radius, length, closed, name or Path(path).stem)


# ---- option 3: XY coordinates (4.2.2.3) ----

def track_from_xy(name, x, y, closed, step=0.5, smooth_m=5.0, moving_average_points=1, max_radius=R_STRAIGHT):
    """Track from a centerline (x, y in metres) in driving order.

    The points are optionally filtered, then resampled every `step` metres, smoothed over `smooth_m` metres, and the
    radius is taken from the curvature of the smoothed line.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    x, y = _smooth(x, moving_average_points, closed), _smooth(y, moving_average_points, closed)
    if closed:
        x, y = np.append(x, x[0]), np.append(y, y[0])
    dist = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    n = int(dist[-1] / step) + (0 if closed else 1)
    s = np.arange(n) * step
    x, y = np.interp(s, dist, x), np.interp(s, dist, y)
    window = smooth_m / step
    x, y = _smooth(x, window, closed), _smooth(y, window, closed)
    dx, dy = _derivative(x, step, closed), _derivative(y, step, closed)
    ddx, ddy = _derivative(dx, step, closed), _derivative(dy, step, closed)
    curvature = (dx * ddy - dy * ddx) / np.maximum(dx ** 2 + dy ** 2, 1e-9) ** 1.5
    curvature = _smooth(curvature, window / 2, closed)
    dl = np.full(n, step)
    if not closed:
        dl[-1] = 0.0
    return _make_track(name, s, curvature, x, y, closed, dl)


def load_track(name, closed, **kwargs):
    """Load tracks/<name>.csv (columns x_m, y_m: the centerline in driving order) with track_from_xy."""
    data = np.loadtxt(HERE / "tracks" / f"{name}.csv", delimiter=",", skiprows=1)
    return track_from_xy(name, data[:, 0], data[:, 1], closed, **kwargs)


# ---- option 2: GPS coordinates (4.2.2.2) ----

def latlon_to_xy(lat, lon):
    """Local XY [m] from latitude / longitude [deg] (flat earth around the first point)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    earth = 6378137.0
    x = np.radians(lon - lon[0]) * earth * math.cos(math.radians(lat[0]))
    y = np.radians(lat - lat[0]) * earth
    return x, y


def fix_start_finish(x, y, method="auto", n_points=0, remove_near=None, near_radius=3.0):
    """Remove the overlap of the start/finish line when the log has more than one lap (thesis 4.2.2.2).

    method "auto"   : the log is cut where it comes back close to its first point
           "npoints": drop n_points from the start and from the end
           "select" : remove every point within near_radius [m] of the (x, y) points in remove_near
                      (the non-interactive version of clicking on the plot)
    Returns the kept x, y.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    if method == "npoints":
        return x[n_points:len(x) - n_points or None], y[n_points:len(y) - n_points or None]
    if method == "select":
        keep = np.ones(len(x), bool)
        for px, py in remove_near or []:
            keep &= np.hypot(x - px, y - py) > near_radius
        return x[keep], y[keep]
    step = np.median(np.hypot(np.diff(x), np.diff(y)))
    close = np.hypot(x - x[0], y - y[0]) < 3 * step
    far_enough = np.arange(len(x)) > len(x) // 2
    candidates = np.nonzero(close & far_enough)[0]
    cut = candidates[0] if len(candidates) else len(x)
    return x[:cut], y[:cut]


def track_from_gps(name, lat, lon, closed=True, outlier_deg=0.01, moving_average_points=5, start_finish="auto",
                   n_points=0, remove_near=None, **xy_options):
    """Track from GPS latitude / longitude [deg] (thesis 4.2.2.2): remove duplicates and outliers, convert to XY,
    handle the start/finish overlap, smooth, then radius from the curvature. xy_options go to track_from_xy."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    keep = np.ones(len(lat), bool)
    keep[1:] = (np.diff(lat) != 0) | (np.diff(lon) != 0)                        # duplicate points
    keep &= (np.abs(lat - np.median(lat)) < outlier_deg) & (np.abs(lon - np.median(lon)) < outlier_deg)
    x, y = latlon_to_xy(lat[keep], lon[keep])
    if closed:
        x, y = fix_start_finish(x, y, start_finish, n_points, remove_near)
    return track_from_xy(name, x, y, closed, moving_average_points=moving_average_points, **xy_options)


# ---- option 4: acceleration & speed (4.2.2.4) ----

def track_from_accel_speed(name, time, speed_kmh, lat_accel_g, closed=True, filter="moving average", window=5,
                           cutoff_hz=2.0, order=2, **xy_options):
    """Track from a log of time [s], speed [km/h] and lateral acceleration [g] (positive = left) (eq 4-7).

    Section length = speed * dt and curvature = ay / V^2. The logged signals are filtered first (moving average or
    Butterworth low-pass). xy_options go to xy_from_radius_length.
    """
    time = np.asarray(time, float)
    v = np.asarray(speed_kmh, float)
    ay = np.asarray(lat_accel_g, float)
    if filter == "moving average":
        v, ay = moving_average(v, window, closed), moving_average(ay, window, closed)
    elif filter == "butterworth":
        fs = 1 / np.median(np.diff(time))
        v, ay = butterworth_lowpass(v, cutoff_hz, fs, order), butterworth_lowpass(ay, cutoff_hz, fs, order)
    dt = np.diff(time, append=time[-1] + np.median(np.diff(time)))
    ms = np.maximum(v / 3.6, 0.5)
    dl = ms * dt
    curvature = ay * G / ms ** 2
    radius = np.where(np.abs(curvature) < 1 / R_STRAIGHT, R_STRAIGHT, 1 / np.where(curvature == 0, 1, curvature))
    return track_from_radius_length(radius, dl, closed, name, **xy_options)


# ---- extra processing (4.2.3) ----

def filter_radius(track, method="moving average", window=5, cutoff_per_m=0.2, order=2):
    """Filter the radius & length data (thesis 4.2.3.1). The curvature (1/R) is filtered, which is the same idea
    as filtering the radius but does not blow up on the very large radius of straights.

    method "moving average": window in points; "butterworth": cutoff in cycles per metre of track.
    """
    k = track.curvature
    if method == "moving average":
        k = moving_average(k, window, track.closed)
    else:
        spacing = np.median(track.dl[track.dl > 0])
        k = butterworth_lowpass(k, cutoff_per_m, 1 / spacing, order)
    radius_signed = 1 / np.where(np.abs(k) < 1 / R_STRAIGHT, 1 / R_STRAIGHT, k)
    return track_from_radius_length(radius_signed, track.dl, track.closed, track.name)


def fine_mesh(track, threshold_radius=35.0, span=2.0, step=0.1):
    """Resample the track with a small step in and around tight corners (thesis 4.2.3.2).

    Every point with a radius below threshold_radius gets the fine mesh, and so does the track within `span` metres
    before and after it. Defaults are the thesis values for Formula Student: 35 m, 2 m, 0.1 m.
    """
    s, n = track.s, len(track.s)
    near = np.nonzero(track.radius < threshold_radius)[0]
    in_region = np.zeros(n, bool)
    if len(near):
        s_near = s[near]
        pos = np.searchsorted(s_near, s)
        left = np.abs(s - s_near[np.clip(pos - 1, 0, len(s_near) - 1)])
        right = np.abs(s - s_near[np.clip(pos, 0, len(s_near) - 1)])
        in_region = np.minimum(left, right) <= span
    new_s = []
    for i in range(n):
        seg = track.dl[i]
        if seg > step and (in_region[i] or in_region[(i + 1) % n]):
            m = int(math.ceil(seg / step))
            new_s.extend(s[i] + np.arange(m) * seg / m)
        else:
            new_s.append(s[i])
    new_s = np.array(new_s)
    if track.closed:
        s_ext = np.append(s, s[0] + track.length)
        k_ext, x_ext, y_ext = np.append(track.curvature, track.curvature[0]), np.append(track.x, track.x[0]), np.append(track.y, track.y[0])
        dl = np.diff(new_s, append=s[0] + track.length)
    else:
        s_ext, k_ext, x_ext, y_ext = s, track.curvature, track.x, track.y
        dl = np.diff(new_s, append=new_s[-1])
    return _make_track(track.name, new_s, np.interp(new_s, s_ext, k_ext), np.interp(new_s, s_ext, x_ext),
                       np.interp(new_s, s_ext, y_ext), track.closed, dl)


def plot_track(track, values=None, label="", ax=None, cmap="turbo"):
    """Track map, coloured by `values` (one number per point) if given (Figure 3-12 style)."""
    import matplotlib.pyplot as plt
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 4))
    if values is None:
        ax.plot(track.x, track.y, "k-")
    else:
        sc = ax.scatter(track.x, track.y, c=values, s=6, cmap=cmap)
        plt.colorbar(sc, ax=ax, label=label)
    ax.set(aspect="equal", title=track.name)
    return ax


# =====================================================================================
# 10. LAPSIM (thesis 4.3, 4.4)
#     1. Maximum cornering speed at every track point (with the apex friction ellipse correction)
#     2. Apexes = local minima of that speed
#     3. Accelerate from every apex, brake (backwards) to every apex
#     4. Final speed = minimum of all traces; re-process the braking points
#     5. Back-calculate the driven channels and the KPIs
# =====================================================================================

def _find_apexes(vmax, s, top_speed, spacing):
    """True apexes (thesis 4.2.4): local minima of the corner speed at least `spacing` metres apart.

    Several minima inside one corner are noise from the track model: the lowest one is the real apex and the
    points between them are interpolated smoothly (4.2.4.3). Returns (apex indices, smoothed vmax).
    """
    vmax = vmax.copy()
    inner = (vmax[1:-1] <= vmax[:-2]) & (vmax[1:-1] < vmax[2:]) & (vmax[1:-1] < 0.999 * top_speed)
    candidates = np.nonzero(inner)[0] + 1
    apexes = []
    for i in candidates[np.argsort(vmax[candidates])]:                  # slowest first
        if all(abs(s[i] - s[a]) >= spacing for a in apexes):
            apexes.append(i)
    apexes.sort()
    for a in apexes:                                                    # smooth the noise around each apex
        group = [c for c in candidates if abs(s[c] - s[a]) < spacing]
        if len(group) > 1:
            lo, hi = min(group), max(group)
            vmax[lo:a + 1] = np.linspace(vmax[lo], vmax[a], a - lo + 1)
            vmax[a:hi + 1] = np.linspace(vmax[a], vmax[hi], hi - a + 1)
    return apexes, vmax


def _accelerate_from(car, i, v_start, vmax, radius, dl, acc_best, wt_on):
    """Combined acceleration from point i until braking is needed or the end (thesis 4.3.2)."""
    v, ax = v_start, 0.0
    acc_best[i] = min(acc_best[i], v)
    for j in range(i, len(vmax) - 1):
        ax_vehicle = combined_accel(car, v, radius[j], ax, True, None, wt_on)
        ax_limited = (vmax[j + 1] ** 2 - v * v) / (2 * dl[j])            # eq 4-11
        ax = min(ax_vehicle, ax_limited)                                  # eq 4-12
        if ax < -0.2:
            return                                                        # braking is needed: a later apex takes over
        ax = max(ax, 0.0)       # right at an apex the acceleration is zero; ignore tiny negative values (table accuracy)
        v = math.sqrt(v * v + 2 * ax * dl[j])
        acc_best[j + 1] = min(acc_best[j + 1], v)


def _brake_to(car, i, vmax, radius, dl, brk_best, wt_on):
    """Combined braking, calculated backwards from apex i (thesis 4.3.3).

    Going backwards the speed rises by the full braking deceleration. Where that would exceed the corner speed limit
    the deceleration is modulated: the trace follows the limit instead. The trace ends when the car would have to
    accelerate to follow the limit (another apex takes over) or at the top speed.
    """
    v, ax = vmax[i], 0.0
    brk_best[i] = min(brk_best[i], v)
    for j in range(i, 0, -1):
        ax = combined_accel(car, v, radius[j], ax, False, None, wt_on)    # negative
        v_next = math.sqrt(v * v - 2 * ax * dl[j - 1])                    # speed one point earlier, full braking
        if v_next >= vmax[j - 1]:
            if vmax[j - 1] < v:
                return                                                    # accelerating would be needed
            v_next = vmax[j - 1]
            ax = (v * v - v_next * v_next) / (2 * dl[j - 1]) * -1.0       # the modulated (smaller) deceleration
        v = v_next
        brk_best[j - 1] = min(brk_best[j - 1], v)
        if v >= car.top_speed - 1e-6:
            return


def _reprocess_braking_points(car, s, radius, x, y, dl, vmax, acc_best, brk_best, wt_on):
    """Add the exact braking points (thesis 4.3.5). Where the final trace switches from an acceleration trace to a
    braking trace, the two traces cross between two track points; that crossing is inserted as a new point."""
    stack = np.vstack([vmax, acc_best, brk_best])
    source = np.argmin(stack, axis=0)
    final = stack.min(axis=0)
    out_s, out_r, out_x, out_y, out_dl, out_v = [], [], [], [], [], []
    n = len(s)
    for i in range(n):
        out_s.append(s[i]); out_r.append(radius[i]); out_x.append(x[i]); out_y.append(y[i]); out_v.append(final[i])
        out_dl.append(dl[i])
        if i < n - 1 and source[i] == 1 and source[i + 1] == 2 and dl[i] > 0:
            # continue each trace one step to compare them on the same interval
            a_ax = combined_accel(car, acc_best[i], radius[i], 0.0, True, None, wt_on)
            v_a1 = math.sqrt(max(acc_best[i] ** 2 + 2 * a_ax * dl[i], 0.0))
            b_ax = combined_accel(car, brk_best[i + 1], radius[i + 1], 0.0, False, None, wt_on)
            v_b0 = math.sqrt(max(brk_best[i + 1] ** 2 - 2 * b_ax * dl[i], 0.0))
            denom = (v_a1 - acc_best[i]) - (brk_best[i + 1] - v_b0)
            tt = (v_b0 - acc_best[i]) / denom if abs(denom) > 1e-12 else 0.5
            if 0.02 < tt < 0.98:
                out_dl[-1] = tt * dl[i]
                out_s.append(s[i] + tt * dl[i])
                out_r.append(1 / ((1 - tt) / radius[i] + tt / radius[i + 1]))
                out_x.append(x[i] + tt * (x[i + 1] - x[i]))
                out_y.append(y[i] + tt * (y[i + 1] - y[i]))
                out_v.append(acc_best[i] + tt * (v_a1 - acc_best[i]))
                out_dl.append((1 - tt) * dl[i])
    return (np.array(out_s), np.array(out_r), np.array(out_x), np.array(out_y), np.array(out_dl), np.array(out_v))


def simulate_lap(car, track, start="standing", apex_spacing=2.0, weight_transfer_on=True, reprocess=True,
                 channels=True):
    """Lap simulation (thesis 4.3).

    start: "standing" (0 m/s at the start line), "flying" (closed track only: the lap starts and ends at the same
           speed, found by starting at the slowest apex), or a speed in m/s.
    Returns {"time", "trace", "track_length"}; trace has the driven channels (4.4.1) when channels=True.
    """
    s, radius, x, y, dl = track.s.copy(), track.radius.copy(), track.x.copy(), track.y.copy(), track.dl.copy()
    vmax_all = np.minimum(corner_speed(car, radius, apex=True), car.top_speed)
    flying = start == "flying"
    if flying and not track.closed:
        raise ValueError('start="flying" needs a closed track; give a speed in m/s for an open track')
    if track.closed:
        if flying:
            k = int(np.argmin(vmax_all))                   # start at the slowest apex: its speed is known
            radius, x, y, dl, vmax_all = (np.roll(a, -k) for a in (radius, x, y, dl, vmax_all))
        s = np.concatenate([[0.0], np.cumsum(dl)[:-1]])
        # close the lap with a copy of the first point (the finish line)
        s, radius, x, y, vmax_all = (np.append(a, a[0]) for a in (s, radius, x, y, vmax_all))
        s[-1] = dl.sum()
        dl = np.append(dl, 0.0)
    n = len(s)
    apexes, vmax = _find_apexes(vmax_all, s, car.top_speed, apex_spacing)
    acc_best = np.full(n, np.inf)
    brk_best = np.full(n, np.inf)
    if flying:
        apexes = sorted(set(apexes) | {0, n - 1})
    for a in apexes:
        _accelerate_from(car, a, vmax[a], vmax, radius, dl, acc_best, weight_transfer_on)
        _brake_to(car, a, vmax, radius, dl, brk_best, weight_transfer_on)
    if not flying:
        v0 = 0.0 if start == "standing" else float(start)
        _accelerate_from(car, 0, min(v0, vmax[0]), vmax, radius, dl, acc_best, weight_transfer_on)
        acc_best[0] = min(v0, vmax[0])
    if reprocess:
        s, radius, x, y, dl, v = _reprocess_braking_points(car, s, radius, x, y, dl, vmax, acc_best, brk_best, weight_transfer_on)
    else:
        v = np.minimum(np.minimum(vmax, acc_best), brk_best)
    if not flying:
        v[0] = min(v[0], 0.0 if start == "standing" else float(start))
    v_mid = np.maximum((v[1:] + v[:-1]) / 2, 0.1)
    seg = dl[:-1]
    time = float(np.sum(seg / v_mid))
    t = np.concatenate([[0.0], np.cumsum(seg / v_mid)])
    s_new = np.concatenate([[0.0], np.cumsum(seg)])
    ax = np.concatenate([(v[1:] ** 2 - v[:-1] ** 2) / (2 * np.maximum(seg, 1e-9)), [0.0]])
    ax[-1] = ax[-2]
    ay = v ** 2 / radius
    result = {"time": time, "track_length": float(s_new[-1]), "start": start}
    trace = driven_channels(car, s_new, t, v, ax, ay) if channels else pd.DataFrame({"s": s_new, "t": t, "v": v, "ax": ax, "ay": ay})
    trace["x"], trace["y"], trace["radius"] = x, y, radius
    result["trace"] = trace
    return result


def lap_kpis(car, result, low_speed_kmh=40.0, high_speed_kmh=60.0, cornering_g=0.25, accel_g=0.05):
    """Key performance indicators of a lap (thesis 4.4.2, Table 4-7). Percentages are shares of the lap time.

    Cornering = more than cornering_g of lateral acceleration; accelerating / decelerating = more than accel_g of
    longitudinal acceleration. Low / medium / high speed use the two speed limits given.
    """
    tr = result["trace"]
    dt = np.diff(tr["t"].values, append=tr["t"].values[-1])
    weight = dt / max(dt.sum(), 1e-9)
    share = lambda mask: float(100 * np.sum(weight[np.asarray(mask)]))
    v = tr["v_kmh"].values
    drive = tr["drive force [N]"].values
    seg = np.diff(tr["s"].values, append=tr["s"].values[-1])
    fuel_j = np.sum(drive * seg) / car.drivetrain_efficiency / car.thermal_efficiency
    gear = tr["gear"].values
    kpi = {
        "LapTime [s]": result["time"], "Track Length [m]": result["track_length"],
        "Low Speed [%]": share(v < low_speed_kmh), "Medium Speed [%]": share((v >= low_speed_kmh) & (v < high_speed_kmh)),
        "High Speed [%]": share(v >= high_speed_kmh),
        "Cornering [%]": share(tr["ay"].values / G > cornering_g),
        "Accelerating [%]": share(tr["ax"].values / G > accel_g), "Decelerating [%]": share(tr["ax"].values / G < -accel_g),
        "Grip Limited [%]": share(tr["grip limited"].values), "Power Limited [%]": share(~tr["grip limited"].values.astype(bool)),
        "Min Gear": int(gear.min()), "Max Gear": int(gear.max()), "Gear Shifts": int(np.sum(np.diff(gear) != 0)),
        "Max Speed [km/h]": float(v.max()), "Min Speed [km/h]": float(v.min()), "Median Speed [km/h]": float(np.median(v)),
        "Fuel [kg]": float(fuel_j / car.fuel_heating_value),
        "Median CzT": float(tr["CzT"].median()), "Median AB [%]": float(tr["AB"].median()), "Median Cx": float(tr["Cx"].median()),
    }
    if tr["FRH [mm]"].notna().any():
        kpi.update({"Min FRH [mm]": float(tr["FRH [mm]"].min()), "Median FRH [mm]": float(tr["FRH [mm]"].median()),
                    "Min RRH [mm]": float(tr["RRH [mm]"].min()), "Median RRH [mm]": float(tr["RRH [mm]"].median())})
    return pd.Series(kpi)


def plot_lap(car, result, track=None):
    """Speed and the main channels against distance (Figure 4-57), and the track map coloured by speed."""
    import matplotlib.pyplot as plt
    tr = result["trace"]
    fig, axes = plt.subplots(5, 1, figsize=(10, 11), sharex=True)
    axes[0].plot(tr.s, tr.v_kmh); axes[0].set_ylabel("speed [km/h]")
    axes[1].plot(tr.s, tr.ax / G, label="ax"); axes[1].plot(tr.s, tr.ay / G, label="ay"); axes[1].set_ylabel("g"); axes[1].legend()
    axes[2].plot(tr.s, tr["TPS [%]"], label="TPS"); axes[2].plot(tr.s, tr["pBrakeF [bar]"], label="brake front [bar]"); axes[2].legend()
    axes[3].plot(tr.s, tr["gear"], label="gear"); axes[3].set_ylabel("gear")
    ax_rpm = axes[3].twinx(); ax_rpm.plot(tr.s, tr["rpm"], color="C1"); ax_rpm.set_ylabel("rpm")
    axes[4].plot(tr.s, tr["steering [deg]"]); axes[4].set_ylabel("steering wheel [deg]"); axes[4].set_xlabel("distance [m]")
    fig.tight_layout()
    return fig


def plot_lap_map(result, column="v_kmh", label=None, cmap="turbo"):
    """Track map of a lap coloured by any channel of the trace, for example "v_kmh", "TPS [%]", "steering [deg]",
    "pBrakeF [bar]", "FRH [mm]" (Figure 3-12, 4-55)."""
    import matplotlib.pyplot as plt
    tr = result["trace"]
    fig, ax = plt.subplots(figsize=(9, 4))
    sc = ax.scatter(tr["x"], tr["y"], c=tr[column], s=6, cmap=cmap)
    plt.colorbar(sc, ax=ax, label=label or column)
    ax.set(aspect="equal", title=column)
    return fig


# =====================================================================================
# 11. SUSPENSION DYNAMICS (thesis chapter 5)
#     Rates calculator, quarter car model, damping curves, parameter sweeps, virtual 7-post rig.
#     The rates also feed the aero ride height calculation (heave stiffness) and the yaw moment diagram.
# =====================================================================================

LBS_IN_TO_N_M = 175.126835      # eq 2-17


def suspension_rates(car):
    """Rates calculator (thesis 5.2, Table 5-1). Returns a dict of the model suspension parameters."""
    s = car.suspension
    wd = car.weight_dist_front / 100
    k_spring_f, k_spring_r = s.spring_front_lbs_in * LBS_IN_TO_N_M, s.spring_rear_lbs_in * LBS_IN_TO_N_M
    kw_f, kw_r = k_spring_f / s.motion_ratio_front ** 2, k_spring_r / s.motion_ratio_rear ** 2          # eq 5-1
    kt_f, kt_r = s.tire_stiffness_front, s.tire_stiffness_rear
    kh_f, kh_r = kw_f * kt_f / (kw_f + kt_f), kw_r * kt_r / (kw_r + kt_r)                                # eq 5-2
    # pitch (eq 5-3, 5-4, 5-5)
    pcx = car.pitch_centre_x
    k_pitch = math.pi / 90 * (pcx ** 2 * kh_f + (car.wheelbase - pcx) ** 2 * kh_r)                       # Nm/deg
    pitch_theta = car.mass * G * car.pitch_arm / k_pitch                                                 # deg/g
    # dynamic pitch centre exactly as printed in the thesis (reproduces Table 5-1)
    pc_dynamic = pcx + pcx * (1 - s.motion_ratio_front ** 2 * k_spring_f / (k_spring_r * s.motion_ratio_rear ** 2))
    # roll (eq 5-6 .. 5-10)
    kw_arb_f, kw_arb_r = s.arb_front / s.motion_ratio_arb_front ** 2, s.arb_rear / s.motion_ratio_arb_rear ** 2
    series = lambda a, b: a * b / (a + b)
    k_roll_f = math.pi * car.front_track ** 2 / 360 * series(kw_f + kw_arb_f, kt_f)
    k_roll_r = math.pi * car.rear_track ** 2 / 360 * series(kw_r + kw_arb_r, kt_r)
    k_roll = k_roll_f + k_roll_r
    rc_cog = (1 - wd) * s.roll_centre_front + wd * s.roll_centre_rear                                    # eq 2-18
    roll_arm = car.cog_sm - rc_cog                                                                       # eq 2-19
    roll_phi = car.mass * G * roll_arm / k_roll                                                          # deg/g
    # single bump (eq 5-11): spring in parallel with (ARB in series with the opposite wheel spring).
    # The thesis writes the series term with reciprocals, so its Table 5-1 shows the heave rate instead.
    sb_param_f = kw_f + series(kw_f, kw_arb_f)
    sb_param_r = kw_r + series(kw_r, kw_arb_r)
    return {
        "wheel_rate_front": kw_f, "wheel_rate_rear": kw_r,
        "heave_stiffness_front": kh_f, "heave_stiffness_rear": kh_r,
        "pitch_stiffness": k_pitch, "pitch_gradient": pitch_theta, "dynamic_pitch_centre": pc_dynamic,
        "arb_wheel_rate_front": kw_arb_f, "arb_wheel_rate_rear": kw_arb_r,
        "roll_stiffness_front": k_roll_f, "roll_stiffness_rear": k_roll_r, "roll_stiffness": k_roll,
        "roll_gradient": roll_phi, "mechanical_balance": 100 * k_roll_f / k_roll,
        "single_bump_front": series(sb_param_f, kt_f), "single_bump_rear": series(sb_param_r, kt_r),
        "roll_axis_height_at_cog": rc_cog, "roll_arm": roll_arm,
    }


def quarter_car(car):
    """Natural frequencies, critical damping and damping coefficients (thesis 5.3, Tables 5-4 .. 5-8)."""
    s, r = car.suspension, car.rates
    kt = (s.tire_stiffness_front, s.tire_stiffness_rear)
    kw = (r["wheel_rate_front"], r["wheel_rate_rear"])
    kh = (r["heave_stiffness_front"], r["heave_stiffness_rear"])
    kw_arb = (r["arb_wheel_rate_front"], r["arb_wheel_rate_rear"])
    m_sm = (car.mass_sm_front, car.mass_sm_rear)
    m_nsm = (car.mass_nsm_front, car.mass_nsm_rear)
    out = {}
    for i, ax in enumerate(("front", "rear")):
        # natural frequency [Hz] (eq 5-12, 5-13); masses are per axle so divide by 2 for one corner
        out[f"natural_freq_sprung_{ax}"] = math.sqrt(kh[i] / (m_sm[i] / 2)) / (2 * math.pi)
        out[f"natural_freq_unsprung_{ax}"] = math.sqrt((kw[i] + kt[i]) / (m_nsm[i] / 2)) / (2 * math.pi)
        # critical damping [Ns/m] (eq 5-14 .. 5-17)
        k_roll_param = (kw[i] + kw_arb[i]) * kt[i] / (kw[i] + kw_arb[i] + kt[i])
        out[f"crit_damping_heave_sprung_{ax}"] = 2 * math.sqrt(kh[i] * m_sm[i] / 2)
        out[f"crit_damping_heave_unsprung_{ax}"] = 2 * math.sqrt((kw[i] + kt[i]) * m_nsm[i] / 2)
        out[f"crit_damping_roll_sprung_{ax}"] = 2 * math.sqrt(k_roll_param * m_sm[i] / 2)
        out[f"crit_damping_roll_unsprung_{ax}"] = 2 * math.sqrt(k_roll_param * m_nsm[i] / 2)
    return out


def damping_coefficients(car):
    """Damping coefficients at the wheel [Ns/m] (thesis 5.3.2.2, Tables 5-7 and 5-8)."""
    s, q = car.suspension, quarter_car(car)
    out = {}
    for ax, z_ls, z_hs in (("front", s.zeta_front_ls, s.zeta_front_hs), ("rear", s.zeta_rear_ls, s.zeta_rear_hs)):
        # heave: low speed uses the sprung mass, high speed the unsprung mass; rebound = compression * ratio
        c_ls = q[f"crit_damping_heave_sprung_{ax}"] * z_ls
        c_hs = q[f"crit_damping_heave_unsprung_{ax}"] * z_hs
        out[f"heave_{ax}"] = {"compression_ls": c_ls, "compression_hs": c_hs,
                              "rebound_ls": c_ls * s.comp_reb_ls, "rebound_hs": c_hs * s.comp_reb_hs}
        # roll: rebound = compression / ratio
        r_ls = q[f"crit_damping_roll_sprung_{ax}"] * z_ls
        r_hs = q[f"crit_damping_roll_unsprung_{ax}"] * z_hs
        out[f"roll_{ax}"] = {"compression_ls": r_ls, "compression_hs": r_hs,
                             "rebound_ls": r_ls / s.comp_reb_ls, "rebound_hs": r_hs / s.comp_reb_hs}
    return out


def damper_force(car, axle, velocity, motion="heave"):
    """Damper force at the wheel [N] for a wheel velocity [m/s] (positive = compression). Damping curve of 5.3.2.3."""
    c = damping_coefficients(car)[f"{motion}_{axle}"]
    knee = car.suspension.knee_speed_mm_s / 1000
    v = np.asarray(velocity, float)
    a = np.abs(v)
    comp = np.where(a <= knee, c["compression_ls"] * a, c["compression_ls"] * knee + c["compression_hs"] * (a - knee))
    reb = np.where(a <= knee, c["rebound_ls"] * a, c["rebound_ls"] * knee + c["rebound_hs"] * (a - knee))
    return np.where(v >= 0, comp, -reb)


def damping_curves(car, max_speed_mm_s=250.0, points=101):
    """Damping curves (force vs. wheel speed) for front/rear, heave/roll (Figure 5-12). Returns a DataFrame."""
    v = np.linspace(-max_speed_mm_s, max_speed_mm_s, points) / 1000
    data = {"speed [mm/s]": v * 1000}
    for ax in ("front", "rear"):
        for motion in ("heave", "roll"):
            data[f"{motion} {ax} [N]"] = damper_force(car, ax, v, motion)
    return pd.DataFrame(data).set_index("speed [mm/s]")


# ---- generic numerical integration: Dormand-Prince 5(4), adaptive step (thesis 5.5.3) ----
_DP_C = (0, 1 / 5, 3 / 10, 4 / 5, 8 / 9, 1, 1)
_DP_A = ((), (1 / 5,), (3 / 40, 9 / 40), (44 / 45, -56 / 15, 32 / 9),
         (19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729),
         (9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656),
         (35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84))
_DP_B5 = (35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84, 0)
_DP_B4 = (5179 / 57600, 0, 7571 / 16695, 393 / 640, -92097 / 339200, 187 / 2100, 1 / 40)


def dormand_prince(f, y0, t_end, rtol=1e-6, atol=1e-9, h0=1e-4, h_max=2e-3):
    """Integrate dy/dt = f(t, y) from 0 to t_end with the Dormand-Prince method and an adaptive step. Returns t, y."""
    t, y, h = 0.0, np.asarray(y0, float), h0
    ts, ys = [t], [y.copy()]
    k1 = f(t, y)
    while t < t_end - 1e-12:
        h = min(h, h_max, t_end - t)
        k = [k1]
        for i in range(1, 7):
            yi = y + h * sum(a * kj for a, kj in zip(_DP_A[i], k))
            k.append(f(t + _DP_C[i] * h, yi))
        y5 = y + h * sum(b * kj for b, kj in zip(_DP_B5, k))
        y4 = y + h * sum(b * kj for b, kj in zip(_DP_B4, k))
        err = np.max(np.abs(y5 - y4) / (atol + rtol * np.maximum(np.abs(y), np.abs(y5))))
        if err <= 1.0:
            t, y, k1 = t + h, y5, k[6]
            ts.append(t)
            ys.append(y.copy())
        h *= min(5.0, max(0.2, 0.9 * err ** -0.2)) if err > 0 else 5.0
    return np.array(ts), np.array(ys)


def quarter_car_response(car, axle="front", bump=0.01, t_end=1.5, motion="heave"):
    """Quarter car model response to a step bump of height `bump` [m] (eq of Figure 5-4). Returns a DataFrame.

    Two masses: sprung (one corner) and unsprung. The damper uses the damping curve of 5.3.2.3.
    """
    s, r = car.suspension, car.rates
    front = axle == "front"
    m_b = (car.mass_sm_front if front else car.mass_sm_rear) / 2
    m_t = (car.mass_nsm_front if front else car.mass_nsm_rear) / 2
    k_b = r["wheel_rate_front"] if front else r["wheel_rate_rear"]
    k_t = s.tire_stiffness_front if front else s.tire_stiffness_rear

    def f(t, y):
        x_b, x_t, v_b, v_t = y                      # displacements up positive
        force = k_b * (x_t - x_b) + float(damper_force(car, axle, v_t - v_b, motion))   # on the body, upward
        return np.array([v_b, v_t, force / m_b, (-force + k_t * (bump - x_t)) / m_t])

    t, y = dormand_prince(f, np.zeros(4), t_end)
    return pd.DataFrame({"t": t, "sprung [mm]": y[:, 0] * 1000, "unsprung [mm]": y[:, 1] * 1000}).set_index("t")


# ---- parameter sweeps (thesis 5.4, 5.5.5, 8.6) ----

def with_params(car, **changes):
    """Copy of the car with some parameters changed. Use dotted names for the sub-models, for example
    with_params(car, mass=240, **{"suspension.spring_front_lbs_in": 400, "tire.mux": 1.3})."""
    top, nested = {}, {}
    for key, value in changes.items():
        if "." in key:
            head, tail = key.split(".", 1)
            nested.setdefault(head, {})[tail] = value
        else:
            top[key] = value
    for head, sub in nested.items():
        top[head] = replace(getattr(car, head), **sub)
    return replace(car, **top)


def parameter_sweep(car, parameter, values, function, **kwargs):
    """Run `function(car, **kwargs)` (it returns a dict of numbers) for several values of one parameter.

    Example: parameter_sweep(car, "suspension.spring_front_lbs_in", range(200, 550, 50), rates_summary)
    """
    rows = []
    for value in values:
        result = function(with_params(car, **{parameter: value}), **kwargs)
        rows.append({parameter: value, **result})
    return pd.DataFrame(rows).set_index(parameter)


def rates_summary(car):
    """Rates calculator results as a flat dict (use with parameter_sweep)."""
    return dict(car.rates)


def quarter_car_summary(car):
    """Natural frequencies and critical damping as a flat dict (use with parameter_sweep)."""
    return quarter_car(car)


# ---- virtual 7-post rig: full car with 7 degrees of freedom (thesis 5.5) ----

def seven_post_matrices(car):
    """Matrices of the full car model: [m] z'' + [Cg] z' + [Kg] z = {Fg}  (eq 5-36 .. 5-44).

    The 7 degrees of freedom are z = [heave, roll, pitch, unsprung LF, RF, LR, RR] (all positive up).
    Returns M, A, K, Kt, B where B = A^T maps z to the suspension deflections x (eq 5-38).
    """
    r = car.rates
    tf, tr = car.front_track / 2, car.rear_track / 2
    lf, lr = car.a_dist, car.b_dist
    M = np.diag([car.mass_sm, car.ixx, car.iyy, car.mass_nsm_front / 2, car.mass_nsm_front / 2,
                 car.mass_nsm_rear / 2, car.mass_nsm_rear / 2])
    A = np.array([[-1, -1, -1, -1], [-tf, tf, -tr, tr], [-lf, -lf, lr, lr],
                  [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)       # eq 5-36.5 (up positive)
    kf, kr = r["wheel_rate_front"], r["wheel_rate_rear"]
    krf, krr = r["arb_wheel_rate_front"], r["arb_wheel_rate_rear"]
    K = np.array([[kf + krf, -krf, 0, 0], [-krf, kf + krf, 0, 0],
                  [0, 0, kr + krr, -krr], [0, 0, -krr, kr + krr]])                       # eq 5-37.3
    s = car.suspension
    Kt = np.diag([0, 0, 0, s.tire_stiffness_front, s.tire_stiffness_front, s.tire_stiffness_rear, s.tire_stiffness_rear])
    return M, A, K, Kt, A.T


def seven_post_free_response(car, mode="heave", amplitude=None, t_end=2.0, damper="linear"):
    """Free response of the full car to an initial heave [mm], roll [deg] or pitch [deg] (thesis 5.5.3).

    amplitude defaults to the thesis examples (15 mm, 1 deg, 0.8 deg).
    damper "linear": one damping coefficient per axle (eq 5-37.1), taken as the mean of the high speed compression and
                     rebound heave coefficients. With the thesis baseline car this reproduces the heave KPIs of
                     Table 5-13 within about 1 %.
    damper "curve" : the full damping curve (compression/rebound, low/high speed, knee speed) of 5.3.2.3.
    Returns a DataFrame with the displacement of every degree of freedom and the vertical load on every tire.
    """
    default = {"heave": 15.0, "roll": 1.0, "pitch": 0.8}
    amplitude = default[mode] if amplitude is None else amplitude
    M, A, K, Kt, B = seven_post_matrices(car)
    Minv = np.linalg.inv(M)
    z0 = np.zeros(7)
    z0[{"heave": 0, "roll": 1, "pitch": 2}[mode]] = amplitude / 1000 if mode == "heave" else math.radians(amplitude)
    axles = ("front", "front", "rear", "rear")

    dc = damping_coefficients(car)
    c_linear = {ax: (dc[f"heave_{ax}"]["compression_hs"] + dc[f"heave_{ax}"]["rebound_hs"]) / 2 for ax in ("front", "rear")}

    def damper_vector(x_dot):
        if damper == "linear":
            return np.array([c_linear[axles[i]] * x_dot[i] for i in range(4)])
        return np.array([float(damper_force(car, axles[i], x_dot[i])) for i in range(4)])

    def f(t, y):
        z, zd = y[:7], y[7:]
        x, x_dot = B @ z, B @ zd
        u = damper_vector(x_dot) + K @ x                  # generalised suspension forces
        zdd = Minv @ (-A @ u - Kt @ z)                     # road input y = 0
        return np.concatenate([zd, zdd])

    t, y = dormand_prince(f, np.concatenate([z0, np.zeros(7)]), t_end)
    z = y[:, :7]
    wd = car.weight_dist_front / 100
    static = np.array([car.mass * G * wd / 2] * 2 + [car.mass * G * (1 - wd) / 2] * 2)
    kt = np.diag(Kt)[3:]
    loads = static + kt * (0 - z[:, 3:])
    out = pd.DataFrame({"heave [mm]": z[:, 0] * 1000, "roll [deg]": np.degrees(z[:, 1]), "pitch [deg]": np.degrees(z[:, 2])}, index=t)
    for i, name in enumerate(("LF", "RF", "LR", "RR")):
        out[f"unsprung {name} [mm]"] = z[:, 3 + i] * 1000
        out[f"tire load {name} [N]"] = loads[:, i]
    out.index.name = "t"
    return out


def seven_post_kpis(response):
    """Tire load KPIs of a free response (thesis 5.5.4, Table 5-13)."""
    loads = response[[c for c in response.columns if c.startswith("tire load")]]
    return {
        "Average Dynamic Tire Load [N]": float(loads.values.mean()),
        "Average Wedge [%]": float(100 * (loads["tire load LF [N]"] + loads["tire load RR [N]"]).mean() / loads.values.sum(axis=1).mean()),
        "Peak Tire Load [N]": float(loads.values.max()),
        "Average Peak Tire Load [N]": float(loads.max().mean()),
        "Negative Peak Tire Load [N]": float(loads.values.min()),
        "Average Negative Peak Tire Load [N]": float(loads.min().mean()),
    }


def seven_post_summary(car, mode="heave", **kwargs):
    """KPIs of a free response as a dict (use with parameter_sweep)."""
    return seven_post_kpis(seven_post_free_response(car, mode, **kwargs))


# ---- anti features from the side view geometry (thesis 7.2.1) ----
# svsa = side view swing arm: tan(angle) = svsa height / svsa length. The result goes into
# Suspension.anti_dive, anti_lift_rear, anti_lift_front and anti_squat (in %).

def anti_dive_percent(car, tan_angle, brake_share_front=None):
    """Front anti-dive [%] (eq 7-2, 7-4). tan_angle = svsa height / svsa length, measured from the tire contact patch
    for outboard brakes and from the wheel centre for inboard brakes. The printed inboard formula divides by the
    braking share instead of multiplying; the standard form (same as outboard) is used here."""
    share = car.brakes.bias_front / 100 if brake_share_front is None else brake_share_front
    return 100 * share * tan_angle / (car.cog_height / car.wheelbase)


def anti_lift_rear_percent(car, tan_angle, brake_share_rear=None):
    """Rear anti-lift in braking [%] (eq 7-3, 7-5)."""
    share = 1 - car.brakes.bias_front / 100 if brake_share_rear is None else brake_share_rear
    return 100 * share * tan_angle / (car.cog_height / car.wheelbase)


def anti_squat_percent(car, tan_angle, drive_share_rear=1.0):
    """Rear anti-squat in acceleration [%] (eq 7-7); drive_share_rear = 1 for a rear-wheel-drive car."""
    return 100 * drive_share_rear * tan_angle / (car.cog_height / car.wheelbase)


def anti_lift_front_percent(car, tan_angle, drive_share_front=0.0):
    """Front anti-lift in acceleration [%] (eq 7-6); zero for a rear-wheel-drive car."""
    return 100 * drive_share_front * tan_angle / (car.cog_height / car.wheelbase)


def plot_tire(car, loads_n=(500.0, 1000.0, 1500.0, 2000.0), cambers_deg=(-2.0, 0.0, 2.0)):
    """Magic Formula check (thesis 8.2.2.3): lateral force and aligning moment against slip angle, for several
    loads (at zero camber) and several cambers (at 1000 N)."""
    import matplotlib.pyplot as plt
    t = car.tire
    slip = np.linspace(-15, 15, 301)
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for fz in loads_n:
        axes[0, 0].plot(slip, pacejka_fy(t.a, fz / 1000, slip, 0.0), label=f"{fz:.0f} N")
        axes[1, 0].plot(slip, pacejka_mz(t.c, fz / 1000, slip, 0.0), label=f"{fz:.0f} N")
    for camber in cambers_deg:
        axes[0, 1].plot(slip, pacejka_fy(t.a, 1.0, slip, camber), label=f"camber {camber:g} deg")
        axes[1, 1].plot(slip, pacejka_mz(t.c, 1.0, slip, camber), label=f"camber {camber:g} deg")
    for ax, title in zip(axes.ravel(), ("Fy vs. load [N]", "Fy vs. camber [N]", "Mz vs. load [Nm]", "Mz vs. camber [Nm]")):
        ax.set(xlabel="slip angle [deg]", title=title)
        ax.axhline(0, color="gray", lw=0.5)
        ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


# =====================================================================================
# 12. AERO MAP TOOLS (thesis chapter 6)
#     Aero viewer, calculation example, straight-line / roll / yaw aero envelopes, static ride height decision.
#     The map itself and its use inside the solvers are in section 2.
# =====================================================================================

def aero_lookup(aero_map, frh, rrh, roll=0.0, yaw=0.0):
    """Step-by-step aero lookup for one car state (thesis 6.2.3, Tables 6-2 .. 6-5).

    Returns a DataFrame with the CzT, AB and Cx after the map, after the roll sensitivity and after the yaw
    sensitivity.
    """
    rows = []
    for label, r, y in (("main map (ride heights)", 0.0, 0.0), ("+ roll sensitivity", roll, 0.0), ("+ roll and yaw sensitivity", roll, yaw)):
        cz, ab, cx = aero_map.coefficients(frh, rrh, r, y)
        rows.append({"step": label, "CzT": float(cz), "AB [%]": float(ab), "Cx": float(cx)})
    return pd.DataFrame(rows).set_index("step")


def plot_aero_map(aero_map, title="AeroMap"):
    """Aero viewer (thesis 6.2): CzT, AB and Cx against front and rear ride height, and the roll and yaw sensitivity."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    f = np.linspace(aero_map.frh[0], aero_map.frh[-1], 60)
    r = np.linspace(aero_map.rrh[0], aero_map.rrh[-1], 60)
    F, R = np.meshgrid(f, r, indexing="ij")
    cz, ab, cx = aero_map.main(F, R)
    for ax, z, name in zip(axes[0], (cz, ab, cx), ("CzT", "AB [% front]", "Cx")):
        im = ax.pcolormesh(R, F, z, shading="auto", cmap="turbo")
        ax.set(xlabel="rear ride height [mm]", ylabel="front ride height [mm]", title=name)
        plt.colorbar(im, ax=ax)
    for ax, sens, name in ((axes[1, 0], aero_map.roll_sens, "roll [deg]"), (axes[1, 1], aero_map.yaw_sens, "yaw [deg]")):
        ax.plot(sens[0], sens[1], "o-", label="CzT change [%]")
        ax.plot(sens[0], sens[3], "s-", label="Cx change [%]")
        ax.plot(sens[0], sens[2], "^-", label="AB change [points]")
        ax.set(xlabel=name, title=f"sensitivity to {name.split()[0]}")
        ax.legend()
    axes[1, 2].axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def aero_envelope(car, speeds_kmh=None, roll=0.0, yaw=0.0):
    """Aero envelope against speed (thesis 6.3). Needs car.aero_map.

    Straight line (roll = yaw = 0): at every speed the ride heights converge with the downforce, using the heave
    stiffness (Figure 6-15). With roll and/or yaw: the sensitivities are applied to the straight-line result
    (6.3.2). Returns a DataFrame with CzT, AB, CzF, CzR, Cx and the ride heights.
    """
    if car.aero_map is None:
        raise ValueError("The aero envelope needs an AeroMap (car.aero_map)")
    mapped = replace(car, aero_mode="map")
    speeds = np.linspace(0, mapped.top_speed * 3.6, 80) if speeds_kmh is None else np.asarray(speeds_kmh, float)
    v = speeds / 3.6
    cz, ab, cx, frh, rrh, bottoming, over_map = _aero_state_arrays(mapped, v, np.zeros_like(v), np.zeros_like(v))
    if roll or yaw:
        cz, ab, cx = mapped.aero_map.coefficients(frh, rrh, roll, yaw)
        cz, cx = cz * mapped.correlation.aero, cx * mapped.correlation.aero
    return pd.DataFrame({"v [km/h]": speeds, "CzT": cz, "AB [%]": ab, "CzF": cz * ab / 100, "CzR": cz * (1 - ab / 100),
                         "Cx": cx, "FRH [mm]": frh, "RRH [mm]": rrh, "bottoming": bottoming, "over aero map": over_map}).set_index("v [km/h]")


def plot_aero_envelope(car, rolls=(0.0, 0.5, 1.0, 1.5), yaws=(0.0, 5.0, 10.0, 15.0, 20.0)):
    """Straight-line, roll and yaw aero envelopes (Figures 6-15 .. 6-22)."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 4, figsize=(18, 8))
    for row, (label, cases) in enumerate((("roll", [(r, 0.0) for r in rolls]), ("yaw", [(0.0, y) for y in yaws]))):
        for roll, yaw in cases:
            env = aero_envelope(car, roll=roll, yaw=yaw)
            tag = f"{label} {roll if label == 'roll' else yaw:g} deg"
            for ax, col in zip(axes[row], ("CzT", "AB [%]", "CzF", "CzR")):
                ax.plot(env.index, env[col], label=tag)
                ax.set(xlabel="speed [km/h]", title=col)
        axes[row, 0].legend(fontsize=8)
    fig.tight_layout()
    return fig


def static_ride_height_sweep(car, front_values_mm, rear_values_mm, speed_range_kmh=(20.0, 100.0), bin_kmh=10.0):
    """Static ride height decision (thesis 6.3.3, Tables 6-6, 6-7). Every front x rear static ride height is tried.

    For each combination the median CzT and AB are taken over the speed range of interest. Combinations that cannot
    reach top speed because the car bottoms out are flagged (the "dashed lines" of the thesis).
    Returns a DataFrame; the best choice is the row with the highest median CzT among the ok ones.
    """
    rows = []
    for f in front_values_mm:
        for r in rear_values_mm:
            test = replace(car, static_rh_front=f, static_rh_rear=r)
            env = aero_envelope(test)
            inside = env[(env.index >= speed_range_kmh[0]) & (env.index <= speed_range_kmh[1])]
            bins = np.arange(speed_range_kmh[0], speed_range_kmh[1] + bin_kmh, bin_kmh)
            czt_bins = [inside[(inside.index >= a) & (inside.index < b)]["CzT"].median() for a, b in zip(bins[:-1], bins[1:])]
            rows.append({"static front RH [mm]": f, "static rear RH [mm]": r,
                         "median CzT": float(np.nanmedian(czt_bins)), "median AB [%]": float(inside["AB [%]"].median()),
                         "ok (no bottoming)": not bool(env["bottoming"].any())})
    return pd.DataFrame(rows)


def plot_ride_height_envelope(result):
    """Ride height "potato" plot of a lap: front against rear ride height, coloured by speed (Figure 7-14)."""
    import matplotlib.pyplot as plt
    tr = result["trace"]
    fig, ax = plt.subplots(figsize=(6, 6))
    sc = ax.scatter(tr["RRH [mm]"], tr["FRH [mm]"], c=tr["v_kmh"], s=6, cmap="turbo")
    plt.colorbar(sc, label="speed [km/h]")
    ax.set(xlabel="rear ride height [mm]", ylabel="front ride height [mm]", title="Ride height envelope")
    return fig


# =====================================================================================
# 13. YAW MOMENT DIAGRAM (thesis chapter 8)
#     Full-car steady-state cornering at many combinations of steer angle and chassis side slip angle, with the
#     Pacejka tire model, lateral weight transfer and aero. Gives grip, balance, stability and control.
#
#     Sign convention used here: positive steer delta = left, positive side slip beta = nose pointing into the
#     turn, positive ay and yaw moment = left turn. The right half of the diagram is left turns.
# =====================================================================================

def lateral_weight_transfer(car, ay, elastic_as_printed=False):
    """Lateral weight transfer of each axle [N] for a lateral acceleration ay [m/s^2] (thesis 8.3.2.3).

    Three parts (eq 8-12 .. 8-15): non-suspended mass, geometric (roll centre height) and elastic (roll stiffness).
    The thesis writes the elastic part with the axle's suspended mass (Mass_SMf, Mass_SMr). The roll moment acts
    on the whole suspended mass, so the default here uses Mass_SM; set elastic_as_printed=True for the printed form.
    Returns (front, rear, LLTD in %).
    """
    s, r = car.suspension, car.rates
    mb = r["mechanical_balance"] / 100
    arm = car.cog_sm - r["roll_axis_height_at_cog"]
    nsm_f = car.mass_nsm_front * ay * car.cog_nsm_front / car.front_track
    nsm_r = car.mass_nsm_rear * ay * car.cog_nsm_rear / car.rear_track
    geo_f = car.mass_sm_front * ay * s.roll_centre_front / car.front_track
    geo_r = car.mass_sm_rear * ay * s.roll_centre_rear / car.rear_track
    m_front = car.mass_sm_front if elastic_as_printed else car.mass_sm
    m_rear = car.mass_sm_rear if elastic_as_printed else car.mass_sm
    el_f = m_front * ay * mb * arm / car.front_track
    el_r = m_rear * ay * (1 - mb) * arm / car.rear_track
    front, rear = nsm_f + geo_f + el_f, nsm_r + geo_r + el_r
    total = front + rear
    lltd = 100 * front / np.where(np.abs(total) < 1e-9, 1.0, total)
    return front, rear, np.where(np.abs(total) < 1e-9, 50.0, lltd)


def _ymd_aero(car, v, ay_abs, beta_abs):
    """CzT, AB, Cx for the yaw moment diagram: constant, roll/yaw sensitivity, or full map (8.3.3)."""
    corr = car.correlation.aero
    if car.aero_mode == "constant":
        shape = np.broadcast(v, ay_abs).shape
        cz, ab, cx = (np.full(shape, car.cz_total * corr), np.full(shape, car.aero_balance_front), np.full(shape, car.cx_total * corr))
    else:
        cz, ab, cx, *_ = _aero_state_arrays(car, v, 0.0, ay_abs, yaw=beta_abs)
    return (cz if car.effects.aero_downforce else 0.0 * cz), ab, cx


def yaw_moment_diagram(car, speed=None, radius=None, delta_range=(-10, 10), beta_range=(-10, 10), step=1.0,
                       tolerance=1e-3, max_iterations=80, elastic_as_printed=False):
    """Yaw moment diagram (thesis 8.4).

    Give `speed` [m/s] for the constant speed diagram, or `radius` [m] for the constant radius diagram.
    For every steer angle delta and side slip angle beta [deg] it iterates until the lateral acceleration
    converges (0.1 %): loads and aero from ay, slip angles, Pacejka forces, new ay (Figure 8-8).
    Returns a dict with the table of results, 2-D arrays and the KPIs.
    """
    if (speed is None) == (radius is None):
        raise ValueError("give either speed (constant speed diagram) or radius (constant radius diagram)")
    deltas = np.arange(delta_range[0], delta_range[1] + step / 2, step)
    betas = np.arange(beta_range[0], beta_range[1] + step / 2, step)
    D, B = np.meshgrid(deltas, betas)                       # shape (n_beta, n_delta)
    t, s = car.tire, car.suspension
    wd = car.weight_dist_front / 100
    left = car.weight_dist_left / 100
    static_front, static_rear = car.mass * G * wd, car.mass * G * (1 - wd)
    ay = np.zeros_like(D)
    done = np.zeros(D.shape, bool)
    iterations = np.zeros(D.shape, int)
    for it in range(max_iterations):
        sign = np.where(ay >= 0, 1.0, -1.0)
        vx = np.full(D.shape, speed) if speed is not None else np.sqrt(np.maximum(np.abs(ay), 0.5) * radius)
        r = sign * np.where(np.abs(ay) < 1e-9, 0.0, np.abs(ay) / vx)       # yaw rate [rad/s], Ay = Vx r  (eq 8-20)
        cz, ab, cx = _ymd_aero(car, vx, np.abs(ay), np.abs(B))
        q = 0.5 * car.air_density * car.frontal_area * vx ** 2
        df_front, df_rear = q * cz * ab / 100, q * cz * (1 - ab / 100)
        wf_front, wf_rear, lltd = lateral_weight_transfer(car, ay, elastic_as_printed)
        fz = {"FL": static_front * left + df_front / 2 - wf_front, "FR": static_front * (1 - left) + df_front / 2 + wf_front,
              "RL": static_rear * left + df_rear / 2 - wf_rear, "RR": static_rear * (1 - left) + df_rear / 2 + wf_rear}   # eq 8-11
        fz = {k: np.maximum(v_, 10.0) for k, v_ in fz.items()}
        # steer angles with static toe and Ackermann (eq 8-8), slip angles (eq 8-9, sign convention above)
        d_fl = D + s.toe_front
        d_fr = D * s.ackermann_ratio - s.toe_front
        vy = -np.tan(np.radians(B)) * vx
        a_dist, b_dist, ft, rt = car.a_dist, car.b_dist, car.front_track, car.rear_track
        alpha = {"FL": d_fl - np.degrees(np.arctan2(vy + r * a_dist, vx - r * ft / 2)),
                 "FR": d_fr - np.degrees(np.arctan2(vy + r * a_dist, vx + r * ft / 2)),
                 "RL": -np.degrees(np.arctan2(vy - r * b_dist, vx - r * rt / 2)),
                 "RR": -np.degrees(np.arctan2(vy - r * b_dist, vx + r * rt / 2))}
        fy, mz = {}, {}
        for k in fz:
            camber = car.camber("front" if k[0] == "F" else "rear")
            fy[k] = pacejka_fy(t.a, fz[k] / 1000, alpha[k], camber) * car.correlation.muy
            mz[k] = pacejka_mz(t.c, fz[k] / 1000, alpha[k], camber)
        ay_new = (fy["FL"] + fy["FR"] + fy["RL"] + fy["RR"]) / car.mass
        change = np.abs(ay_new - ay) <= tolerance * np.maximum(np.abs(ay_new), 0.1)
        iterations[~done] = it + 1
        done |= change
        ay = np.where(done, ay_new, 0.5 * ay + 0.5 * ay_new)
        if done.all():
            break
    yaw_moment = (fy["FL"] + fy["FR"]) * a_dist - (fy["RL"] + fy["RR"]) * b_dist - (mz["FL"] + mz["FR"] + mz["RL"] + mz["RR"])   # eq 8-7
    table = pd.DataFrame({"delta [deg]": D.ravel(), "beta [deg]": B.ravel(), "ay [g]": (ay / G).ravel(),
                          "yaw moment [Nm]": yaw_moment.ravel(), "iterations": iterations.ravel(), "converged": done.ravel(),
                          "CzT": cz.ravel(), "AB [%]": ab.ravel(), "LLTD [%]": np.broadcast_to(lltd, D.shape).ravel()})
    result = {"table": table, "delta": deltas, "beta": betas, "ay": ay / G, "yaw_moment": yaw_moment,
              "speed": speed, "radius": radius, "convergence [%]": float(100 * done.mean())}
    result["kpis"] = ymd_kpis(result)
    return result


def ymd_kpis(result):
    """Grip, balance, stability and control from a yaw moment diagram (thesis 8.5).

    Balance = yaw moment at the maximum lateral acceleration [Nm] (positive = oversteer for left turns).
    Grip max = maximum lateral acceleration [g]; grip trim = lateral acceleration where the balance is zero.
    Stability = d(yaw moment) / d(beta) at constant steer; control = d(yaw moment) / d(delta) at constant beta,
    both at corner entry (zero steer / zero beta) and at the apex (the maximum lateral acceleration point).
    """
    ay, yaw = result["ay"], result["yaw_moment"]
    deltas, betas = result["delta"], result["beta"]
    i_max = np.unravel_index(np.argmax(ay), ay.shape)                      # (beta index, delta index)
    grip_max = float(ay[i_max])
    balance = float(yaw[i_max])
    # grip trim: upper envelope of ay against yaw moment, evaluated at zero yaw moment
    pos = ay > 0
    edges = np.linspace(yaw[pos].min(), yaw[pos].max(), 61)
    centres, env = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = pos & (yaw >= lo) & (yaw <= hi)
        if m.any():
            centres.append(yaw[m].mean())
            env.append(ay[m].max())
    grip_trim = float(np.interp(0.0, centres, env))

    def slope(axis, ib, idl):
        """Central finite difference of the yaw moment along beta (axis 0) or delta (axis 1) at index (ib, idl)."""
        if axis == 0:
            lo, hi = max(ib - 1, 0), min(ib + 1, len(betas) - 1)
            return float((yaw[hi, idl] - yaw[lo, idl]) / (betas[hi] - betas[lo]))
        lo, hi = max(idl - 1, 0), min(idl + 1, len(deltas) - 1)
        return float((yaw[ib, hi] - yaw[ib, lo]) / (deltas[hi] - deltas[lo]))

    i_b0 = int(np.argmin(np.abs(betas)))
    i_d0 = int(np.argmin(np.abs(deltas)))
    return {"Grip Max [g]": grip_max, "Grip Trim [g]": grip_trim, "Balance [Nm]": balance,
            "Stability Entry [Nm/deg]": slope(0, i_b0, i_d0), "Stability Apex [Nm/deg]": slope(0, i_max[0], i_max[1]),
            "Control Entry [Nm/deg]": slope(1, i_b0, i_d0), "Control Apex [Nm/deg]": slope(1, i_max[0], i_max[1])}


def ymd_summary(car, speed=25.0, **kwargs):
    """Yaw moment diagram KPIs as a dict (use with parameter_sweep, thesis 8.6)."""
    return yaw_moment_diagram(car, speed=speed, **kwargs)["kpis"]


def ymd_speed_sensitivity(car, speeds, **kwargs):
    """KPIs of the yaw moment diagram at several speeds [m/s] (thesis 8.4.3.2, Figures 8-14, 8-15)."""
    rows = {v: yaw_moment_diagram(car, speed=v, **kwargs)["kpis"] for v in speeds}
    return pd.DataFrame(rows).T.rename_axis("speed [m/s]")


def plot_yaw_moment_diagram(*results, labels=None):
    """Plot one or more yaw moment diagrams: lines of constant beta (red) and constant delta (blue) (Figure 8-9)."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 7))
    for n, res in enumerate(results):
        for i in range(res["ay"].shape[0]):
            ax.plot(res["ay"][i], res["yaw_moment"][i], color="tab:red", alpha=0.5, lw=0.8)
        for j in range(res["ay"].shape[1]):
            ax.plot(res["ay"][:, j], res["yaw_moment"][:, j], color="tab:blue", alpha=0.5, lw=0.8)
        k = res["kpis"]
        imax = np.unravel_index(np.argmax(res["ay"]), res["ay"].shape)
        label = labels[n] if labels else f"{n + 1}"
        ax.plot(res["ay"][imax], res["yaw_moment"][imax], "*", ms=14, label=f"{label}: grip {k['Grip Max [g]']:.2f} g, balance {k['Balance [Nm]']:.0f} Nm")
    ax.axhline(0, color="k", lw=0.6)
    ax.axvline(0, color="k", lw=0.6)
    ax.set(xlabel="lateral acceleration [g]", ylabel="yaw moment [Nm]", title="Yaw moment diagram (red: beta, blue: delta)")
    ax.legend()
    return fig


# =====================================================================================
# 14. CORRELATION (thesis 4.5)
#     Match the sim to the real car by adding correction factors one at a time:
#     aero, engine power, longitudinal grip, load sensitivity (Tables 4-9 .. 4-14).
# =====================================================================================

def correlation_study(car, track, steps, start="flying", target_time=None, **lap_options):
    """Lap time after adding correction factors one step at a time (thesis Tables 4-9 .. 4-13).

    steps is a list of (name, {Correlation field: value}); each step keeps the earlier ones. Example (thesis):
        [("Aero", {"aero": 0.5}), ("Engine power", {"engine_power": 0.8}),
         ("Longitudinal grip", {"mux_accel": 0.9, "mux_brake": 0.9}), ("Load sensitivity", {"load_sensitivity": 3})]
    Returns a table with the lap time of every step and the difference to target_time (the real lap time).
    """
    rows, cumulative = [], {}
    labelled = [("Initial", {})] + list(steps)
    for name, changes in labelled:
        cumulative.update(changes)
        stepped = replace(car, correlation=replace(car.correlation, **cumulative))
        lap = simulate_lap(stepped, track, start, channels=False, **lap_options)["time"]
        row = {"step": name, "lap time [s]": lap}
        if target_time:
            row["difference to target [%]"] = 100 * (lap - target_time) / target_time
        rows.append(row)
    return pd.DataFrame(rows).set_index("step")


def fit_correlation(car, field, target, run, low=0.3, high=1.5, tolerance=1e-4):
    """Find the value of one Correlation factor that makes run(car) equal to target (bisection).

    run(car) returns a number, usually an event or lap time. Use it on the isolated scenarios first, as in the
    thesis (3.2.3, 3.3.3, 3.4.2): for example fit "mux_accel" to a measured 75 m acceleration time:
        fit_correlation(car, "mux_accel", 4.40, lambda c: run_acceleration(c, distance=75)["time"])
    Returns the factor. The result must change monotonically with the factor between low and high.
    """
    def value(x):
        return run(replace(car, correlation=replace(car.correlation, **{field: x}))) - target
    f_low, f_high = value(low), value(high)
    if f_low * f_high > 0:
        raise ValueError(f"target {target} is not reached with {field} between {low} and {high}")
    for _ in range(40):
        mid = (low + high) / 2
        f_mid = value(mid)
        if abs(f_mid) < tolerance:
            return mid
        if f_low * f_mid <= 0:
            high = mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def compare_traces(sim_trace, measured_distance_m, measured_speed_kmh):
    """Compare a simulated speed trace with a logged one on the same distance axis (thesis 4.5, Figure 4-61).

    Returns a DataFrame with distance, simulated speed, measured speed and the difference [km/h].
    """
    s = np.asarray(sim_trace["s"])
    measured = np.interp(s, measured_distance_m, measured_speed_kmh)
    return pd.DataFrame({"s [m]": s, "sim [km/h]": sim_trace["v_kmh"].values, "measured [km/h]": measured,
                         "difference [km/h]": sim_trace["v_kmh"].values - measured}).set_index("s [m]")


def thesis_checks():
    """Check this code against numbers printed in the thesis, using the thesis baseline car (the Car defaults).

    Returns a table: quantity, thesis value, value from this code, difference in %. Everything except the 7-post rows
    reproduces the thesis to the printed precision; the 7-post rows depend on the damper model and agree within a few %.
    """
    car = Car()
    simple = Car(tire=Tire(lateral_model="simple"))      # the thesis skidpad uses the simple friction model
    r, q, dc = car.rates, quarter_car(car), damping_coefficients(car)
    bp = brake_pressure(car, 2 * G)
    kp = seven_post_kpis(seven_post_free_response(car, "heave"))
    rows = [
        ("Wheel rate front [N/m] (Table 5-1)", 52550, r["wheel_rate_front"]),
        ("Heave stiffness front [N/m]", 33834, r["heave_stiffness_front"]),
        ("Heave stiffness rear [N/m]", 18762, r["heave_stiffness_rear"]),
        ("Pitch stiffness [Nm/deg]", 1259, r["pitch_stiffness"]),
        ("Pitch gradient [deg/g]", 0.2936, r["pitch_gradient"]),
        ("Dynamic pitch centre [m]", -0.3931, r["dynamic_pitch_centre"]),
        ("Roll stiffness [Nm/deg]", 1066.4, r["roll_stiffness"]),
        ("Roll gradient [deg/g]", 0.6328, r["roll_gradient"]),
        ("Mechanical balance [% front]", 56.05, r["mechanical_balance"]),
        ("Sprung natural frequency front [Hz] (Table 5-4)", 4.1, q["natural_freq_sprung_front"]),
        ("Sprung natural frequency rear [Hz]", 3.0, q["natural_freq_sprung_rear"]),
        ("Unsprung natural frequency front [Hz]", 19.3, q["natural_freq_unsprung_front"]),
        ("Critical damping heave sprung front [Ns/m] (Table 5-5)", 2626, q["crit_damping_heave_sprung_front"]),
        ("Critical damping roll sprung rear [Ns/m] (Table 5-6)", 2935, q["crit_damping_roll_sprung_rear"]),
        ("Heave compression LS front [Ns/m] (Table 5-7)", 10505, dc["heave_front"]["compression_ls"]),
        ("Heave rebound HS rear [Ns/m]", 913, dc["heave_rear"]["rebound_hs"]),
        ("Roll rebound LS front [Ns/m] (Table 5-8)", 8049, dc["roll_front"]["rebound_ls"]),
        ("Brake pressure front at 2 g [bar] (Table 2-10)", 45.7, bp[0]),
        ("Brake pressure rear at 2 g [bar]", 66.0, bp[1]),
        ("Skidpad time [s] (Table 3-13)", 4.694, run_cornering(simple)["time"]),
        ("7-post heave: peak tire load [N] (Table 5-13)", 846, kp["Peak Tire Load [N]"]),
        ("7-post heave: negative peak tire load [N]", 215, kp["Negative Peak Tire Load [N]"]),
    ]
    out = pd.DataFrame(rows, columns=["quantity", "thesis", "this code"])
    out["difference [%]"] = 100 * (out["this code"] - out["thesis"]) / out["thesis"].abs()
    return out.set_index("quantity")


# =====================================================================================
# 15. COMPETITION POINTS (FSAE rules D.9 - D.12 and the 2026 Michigan results)
# =====================================================================================

def load_default_tracks(fine=True):
    """Autocross and endurance tracks digitized from the competition maps (approximate), with the fine mesh."""
    tracks = {"autocross": load_track("autocross", closed=False), "endurance": load_track("endurance", closed=True)}
    return {k: fine_mesh(v) for k, v in tracks.items()} if fine else tracks


def run_all_events(car, tracks=None, weight_transfer_on=True):
    """Run the four dynamic events. Returns {event: result dict} (each has "time" in seconds).

    acceleration: 75 m standing start       skidpad: one lap of the 9.125 m circle
    autocross   : one standing-start run    endurance: standing first lap, then flying laps for 22 km
    """
    tracks = tracks or load_default_tracks()
    endurance = tracks["endurance"]
    first = simulate_lap(car, endurance, "standing", weight_transfer_on=weight_transfer_on, channels=False)
    flying = simulate_lap(car, endurance, "flying", weight_transfer_on=weight_transfer_on, channels=False)
    laps = 22000.0 / endurance.length
    return {
        "acceleration": run_acceleration(car, distance=75.0, weight_transfer_on=weight_transfer_on),
        "skidpad": run_cornering(car, radius=9.125, rotation=360.0),
        "autocross": simulate_lap(car, tracks["autocross"], "standing", weight_transfer_on=weight_transfer_on, channels=False),
        "endurance": {"time": first["time"] + (laps - 1) * flying["time"], "laps": laps,
                      "first_lap": first["time"], "flying_lap": flying["time"]},
    }


# Tmax is this factor times the fastest time in the field; score = scale * (...) + floor
# score = scale * ((Tmax/T)**power - 1) / ((Tmax/Tmin)**power - 1) + floor
SCORING = {
    #               Tmax factor, scale, floor, power
    "acceleration": (1.50, 95.5, 4.5, 1),   # D.9.4   (max 100)
    "skidpad":      (1.25, 71.5, 3.5, 2),   # D.10.4  (max 75)
    "autocross":    (1.45, 118.5, 6.5, 1),  # D.11.4  (max 125)
    "endurance":    (1.45, 250.0, 0.0, 1),  # D.12.13 (max 250, plus 25 for finishing all laps)
}
ENDURANCE_LAPS_POINTS = 25   # D.12.13.3: points for completing all laps


def load_results(folder=HERE / "data" / "results_2026"):
    """Read the 2026 Michigan result tables (overall + the four dynamic events)."""
    tables = {name: pd.read_csv(Path(folder) / f"{name}.csv") for name in ("overall", "acceleration", "skidpad", "autocross", "endurance")}
    for col in ("Cost", "Presentation", "Design", "Acceleration", "SkidPad", "Autocross", "Endurance", "Efficiency", "Total"):
        tables["overall"][col] = pd.to_numeric(tables["overall"][col], errors="coerce")
    return tables


def field_best_times(results):
    """Fastest corrected time of the 2026 field in each event (Tmin)."""
    def best(df, col):
        return pd.to_numeric(df[col], errors="coerce").min()
    return {
        "acceleration": best(results["acceleration"], "BestTime"),
        "skidpad": best(results["skidpad"], "BestTime"),
        "autocross": best(results["autocross"], "BestTime"),
        "endurance": best(results["endurance"], "AdjustedTime"),
    }


def event_points(event, time, t_min, cap=True):
    """Points for a corrected time. t_min is the fastest time of the field.

    cap=True follows the rules: if you beat t_min you become Tmin and score the maximum.
    cap=False keeps t_min fixed, so points can go above the maximum (useful to compare designs
    that are all faster than the field).
    """
    factor, scale, floor, power = SCORING[event]
    if cap:
        t_min = min(t_min, time)
    t_max = factor * t_min
    if time > t_max:
        return floor
    return scale * ((t_max / time) ** power - 1) / ((t_max / t_min) ** power - 1) + floor


def time_for_points(event, points, t_min):
    """Inverse of event_points: the time needed to score `points` when the field's fastest time is t_min."""
    factor, scale, floor, power = SCORING[event]
    t_max = factor * t_min
    share = (points - floor) / scale * ((t_max / t_min) ** power - 1)
    return t_max / (1 + share) ** (1 / power)


def max_points(event):
    factor, scale, floor, power = SCORING[event]
    return scale + floor


def points_2026(events, results=None, efficiency=0.0, car_number=100):
    """Points the car would have earned at Michigan 2026, given simulated event results.

    Static events (cost, presentation, design) are the team's real 2026 scores. Efficiency is a number
    you choose (default 0) because the lapsim does not model fuel or energy use.
    Returns (table, summary). table has one row per dynamic event; summary has total score and place.
    """
    results = results or load_results()
    t_min = field_best_times(results)
    overall = results["overall"]
    us = overall[overall["CarNum"] == car_number].iloc[0]
    actual = {"acceleration": us["Acceleration"], "skidpad": us["SkidPad"], "autocross": us["Autocross"], "endurance": us["Endurance"]}

    rows, dynamic = [], 0.0
    for event in SCORING:
        time = events[event]["time"] if isinstance(events[event], dict) else events[event]
        pts = event_points(event, time, t_min[event])
        if event == "endurance":
            pts += ENDURANCE_LAPS_POINTS
        dynamic += pts
        rows.append({"event": event, "sim time [s]": time, "field best 2026 [s]": t_min[event],
                     "sim points": pts, "actual 2026 points": actual[event], "max points": max_points(event) + (ENDURANCE_LAPS_POINTS if event == "endurance" else 0)})
    static = us["Cost"] + us["Presentation"] + us["Design"]
    total = dynamic + static + efficiency
    place = 1 + int((overall["Total"] > total).sum())
    summary = {"total": total, "place": place, "teams": len(overall), "actual total": us["Total"], "actual place": int(us["Place"]) if str(us["Place"]).isdigit() else us["Place"],
               "dynamic points": dynamic, "static points": static}
    return pd.DataFrame(rows).set_index("event"), summary


# =====================================================================================
# 16. DESIGN TOOLS
# =====================================================================================

def points_per_percent(event, time, t_min, percent=1.0):
    """Points gained by making the event `percent` % faster, against a fixed field. Use it to rank design goals."""
    return event_points(event, time * (1 - percent / 100), t_min, cap=False) - event_points(event, time, t_min, cap=False)


def goal_times(target_points, results=None):
    """Event times needed to reach target_points in each dynamic event (against the 2026 field)."""
    t_min = field_best_times(results or load_results())
    rows = {}
    for event, pts in target_points.items():
        pts_time = pts - (ENDURANCE_LAPS_POINTS if event == "endurance" else 0)
        rows[event] = {"target points": pts, "needed time [s]": time_for_points(event, pts_time, t_min[event])}
    return pd.DataFrame(rows).T


def effects_study(car, run, effects=None):
    """What is each effect worth? Flip the switches in car.effects one at a time and re-run.

    run(car) returns a number, usually a time, for example
        lambda c: simulate_lap(c, tracks["endurance"], "flying", channels=False)["time"]
    effects is a list of Effects field names (default: all). Returns a table with the value of the base car, the value
    with that one switch flipped, and the change.
    """
    base = run(car)
    rows = {}
    for name in effects or [f for f in Effects.__dataclass_fields__]:
        current = getattr(car.effects, name)
        value = run(with_params(car, **{f"effects.{name}": not current}))
        rows[name] = {"now": current, "flipped": not current, "base": base, "result when flipped": value,
                      "change": value - base, "change [%]": 100 * (value - base) / base}
    return pd.DataFrame(rows).T.rename_axis("effect")


def compare_designs(base_car, changes, tracks=None, results=None):
    """What-if: run all events for base_car and for a copy with `changes` applied (a dict of Car fields).

    Dotted names reach the sub-models, for example {"mass": 235, "suspension.spring_front_lbs_in": 400}.
    Returns a table with the time and points of both designs and the difference.
    """
    tracks = tracks or load_default_tracks()
    new_car = with_params(base_car, **changes)
    base_table, base_sum = points_2026(run_all_events(base_car, tracks), results)
    new_table, new_sum = points_2026(run_all_events(new_car, tracks), results)
    out = pd.DataFrame({
        "base time [s]": base_table["sim time [s]"], "new time [s]": new_table["sim time [s]"],
        "base points": base_table["sim points"], "new points": new_table["sim points"]})
    out["time change [s]"] = out["new time [s]"] - out["base time [s]"]
    out["points change"] = out["new points"] - out["base points"]
    out.loc["TOTAL", ["base points", "new points", "points change"]] = [base_sum["total"], new_sum["total"], new_sum["total"] - base_sum["total"]]
    return out
