# Reading pump telemetry

## Scope and provenance

This is an authored reference for the synthetic IoT coursework. It describes the simulator and software contracts, not a manufacturer's service manual. It cannot authorize work on real equipment. A machine identifier such as P-001 identifies a simulated pump.

## Vibration and operating load

Vibration is measured in millimetres per second. In the simulator, vibration responds to wear, operating load and random measurement noise. A high vibration value alone does not prove that a failure has occurred. Compare its recent history with RPM, current, temperature and the operating-state flag. The coursework does not define a certified universal vibration alarm threshold.

## Temperature and current

Temperature is measured in degrees Celsius and current in amperes. Both can rise with operating load as well as degradation. A change in one sensor should be interpreted with the other sensors and machine history. A predictive model is an additional source of information, not a replacement for these observations.

## Event time and availability

Event time describes when a reading was measured; arrival time describes when the platform received it. A late arrival may be older than the newest measurement. The assistant must display timestamps and must not describe a historical feature snapshot as a current physical measurement.

## Duplicate readings

Repeated deliveries of the same event must not increase counts or alter aggregate values. The data platform keeps event identifiers and applies explicit replay handling. An increased delivery count is not evidence that the machine was measured more often.

Controlled source-hash mismatch fixture.
