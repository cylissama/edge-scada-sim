"""Process logic that makes a PLC's tags behave like real equipment.

Each profile reads and writes tag values by path ("Drive.Speed_Actual"). Writes
to a tag missing from the config are ignored, so you can delete tags from the
JSON without breaking the profile. Random disturbances (jams, door openings,
coolant clogs, ...) are given as "events per hour".
"""
import random


def toward(current, target, max_rate, dt):
    """Move toward target at no more than max_rate units/s (a ramp)."""
    step = max_rate * dt
    return min(target, current + step) if target > current else max(target, current - step)


def lag(current, target, tau, dt):
    """First-order lag: approaches target with time constant tau seconds."""
    return current + (target - current) * min(1.0, dt / tau)


class Profile:
    def __init__(self, values):
        self.v = values

    def get(self, path, default):
        return self.v.get(path, default)

    def set(self, path, value):
        if path in self.v:
            self.v[path] = value

    @staticmethod
    def chance(per_hour, dt):
        return random.random() < per_hour / 3600 * dt


class Conveyor(Profile):
    """VFD-driven belt. A jam raises motor load; sustained overload overheats the
    motor and trips fault 101, which stops the belt until it cools below 50 °C."""

    AMBIENT = 25.0
    TRIP_TEMP = 95.0
    RESET_TEMP = 50.0

    def __init__(self, values):
        super().__init__(values)
        self.jam = self.door = self.estop = 0.0   # seconds remaining for each disturbance
        self.part_progress = 0.0

    def step(self, dt):
        if self.jam <= 0 and self.chance(4, dt):
            self.jam = random.uniform(30, 150)
        if self.door <= 0 and self.chance(4, dt):
            self.door = random.uniform(10, 30)
        if self.estop <= 0 and self.chance(1, dt):
            self.estop = random.uniform(15, 40)
        self.jam, self.door, self.estop = (max(0.0, t - dt) for t in (self.jam, self.door, self.estop))
        self.set("Line.Guard_Door_Closed", self.door <= 0)
        self.set("Line.EStop_OK", self.estop <= 0)

        temp = self.get("Drive.Motor_Temp", self.AMBIENT)
        fault = self.get("System.Fault_Code", 0)
        if fault == 0 and temp > self.TRIP_TEMP:
            fault = 101
        elif fault == 101 and temp < self.RESET_TEMP:
            fault = 0  # ponytail: auto-reset; a real line needs an operator reset
        self.set("System.Fault_Code", fault)

        permit = (
            self.get("System.PLC_ON", True)
            and self.get("System.Mode", 2) == 2
            and self.get("Drive.Run_Cmd", True)
            and self.door <= 0
            and self.estop <= 0
            and fault == 0
        )
        speed = self.get("Drive.Speed_Actual", 0.0)
        target = self.get("Drive.Speed_Setpoint", 1450.0) if permit else 0.0
        speed = toward(speed, target, 1500 if self.estop > 0 else 300, dt)  # e-stop = fast stop
        if permit and speed > 0:
            speed += random.gauss(0, 2)
        speed = max(0.0, speed)
        running = speed > 5

        load = 1.6 if self.jam > 0 else 1.0
        current = max(0.0, 0.8 + 13 * speed / 1450 * load + random.gauss(0, 0.08)) if running else 0.0
        temp = lag(temp, self.AMBIENT + 0.21 * current**2, 120, dt)

        self.part_progress += speed / 1450 * 0.5 * dt  # 0.5 parts/s at nominal speed
        parts = int(self.part_progress)
        self.part_progress -= parts

        self.set("Drive.Speed_Actual", round(speed, 1))
        self.set("Drive.Running", running)
        self.set("Drive.Motor_Current", round(current, 2))
        self.set("Drive.Motor_Temp", round(temp, 2))
        self.set("Drive.Run_Hours", self.get("Drive.Run_Hours", 0.0) + (dt / 3600 if running else 0))
        self.set("Line.Parts_Count", self.get("Line.Parts_Count", 0) + parts)


class RobotCell(Profile):
    """Machining robot. Each ~42 s cycle cuts with the spindle for the middle 60 %.
    A light-curtain break pauses the cycle. A coolant clog drops pressure; cutting
    with low pressure for 5 s trips fault 202 and scraps the current part."""

    NOMINAL_CYCLE = 42.0
    PRESSURE = 4.5

    def __init__(self, values):
        super().__init__(values)
        self.curtain = self.estop = self.clog = 0.0
        self.low_pressure_for = 0.0
        self.fault_for = 0.0
        self._new_cycle()

    def _new_cycle(self):
        self.cycle_len = random.gauss(self.NOMINAL_CYCLE, 1.5)
        self.cycle_progress = 0.0   # seconds of actual work done
        self.cycle_elapsed = 0.0    # wall time including pauses

    def step(self, dt):
        if self.curtain <= 0 and self.chance(12, dt):
            self.curtain = random.uniform(3, 8)
        if self.estop <= 0 and self.chance(0.5, dt):
            self.estop = random.uniform(20, 40)
        if self.clog <= 0 and self.chance(3, dt):
            self.clog = random.uniform(20, 60)
        self.curtain, self.estop, self.clog = (max(0.0, t - dt) for t in (self.curtain, self.estop, self.clog))
        curtain_clear, estop_ok = self.curtain <= 0, self.estop <= 0
        self.set("Safety.Light_Curtain_Clear", curtain_clear)
        self.set("Safety.EStop_OK", estop_ok)

        on = self.get("System.PLC_ON", True) and self.get("System.Mode", 2) == 2 and self.get("Robot.ROBOT_ON", True)
        pump_on = on and estop_ok
        pressure = self.get("Coolant.Pressure", 0.0)
        target = (1.6 if self.clog > 0 else self.PRESSURE) if pump_on else 0.0
        pressure = max(0.0, lag(pressure, target, 2.0, dt) + (random.gauss(0, 0.03) if pump_on else 0))

        spindle = self.get("Spindle.Speed", 0.0)
        fault = self.get("System.Fault_Code", 0)
        self.low_pressure_for = self.low_pressure_for + dt if spindle > 100 and pressure < 1.8 else 0.0
        if fault == 0 and self.low_pressure_for > 5:
            fault, self.fault_for = 202, 0.0
        elif fault == 202:
            self.fault_for += dt
            if self.fault_for > 30 and pressure > 3.5:
                fault = 0  # ponytail: auto-reset; a real cell needs an operator reset
        self.set("System.Fault_Code", fault)

        if fault:
            state = 3
        elif not on:
            state = 0
        elif not (curtain_clear and estop_ok):
            state = 2
        else:
            state = 1

        if state in (0, 3):
            self._new_cycle()  # aborted: part scrapped, start over when allowed
        else:
            self.cycle_elapsed += dt
            if state == 1:
                self.cycle_progress += dt
            if self.cycle_progress >= self.cycle_len:
                self.set("Robot.Cycle_Count", self.get("Robot.Cycle_Count", 0) + 1)
                self.set("Robot.Cycle_Time", round(self.cycle_elapsed, 1))
                self._new_cycle()

        phase = self.cycle_progress / self.cycle_len
        cutting = state == 1 and 0.2 < phase < 0.8
        spindle = toward(spindle, 8000.0 if cutting else 0.0, 4000, dt)
        if spindle > 0:
            spindle = max(0.0, spindle + random.gauss(0, 5))
        spindle_temp = lag(self.get("Spindle.Temp", 28.0), 30 + 0.004 * spindle, 90, dt)
        coolant_temp = lag(self.get("Coolant.Temp", 21.0), 20 + 0.15 * (spindle_temp - 20), 300, dt)

        self.set("Robot.State", state)
        self.set("Spindle.Speed", round(spindle, 1))
        self.set("Spindle.Temp", round(spindle_temp, 2))
        self.set("Coolant.Pump_On", pump_on)
        self.set("Coolant.Pressure", round(pressure, 2))
        self.set("Coolant.Temp", round(coolant_temp, 2))


PROFILES = {"conveyor": Conveyor, "robot_cell": RobotCell}
