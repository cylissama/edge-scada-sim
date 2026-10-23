import math

import imu_sim


def test_tilt_is_physically_consistent():
    imu_sim.ACCEL_NOISE = imu_sim.GYRO_NOISE = 0.0
    for axis in ("x", "y"):
        imu_sim.TILT_AXIS = axis
        accel, gyro = imu_sim.simulate(0.0)  # theta = 0, rotating fastest
        assert math.isclose(accel["z"], imu_sim.G)
        peak = imu_sim.TILT_AMPLITUDE * 2 * math.pi * imu_sim.TILT_FREQUENCY
        assert math.isclose(gyro[axis], peak)
        accel, _ = imu_sim.simulate(0.5 / imu_sim.TILT_FREQUENCY / 2)  # quarter period: max tilt
        assert math.isclose(math.hypot(*accel.values()), imu_sim.G)  # gravity magnitude preserved
