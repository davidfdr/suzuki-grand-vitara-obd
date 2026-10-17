# Idle analysis of logs/idle_5min.csv
80 samples over 297 s, one full pass every 3.8 s

## Engine speed
RPM: mean 647 rpm, min 635, max 656, sd 4
Idle is steady.

## Temperatures
Coolant: mean 95 °C, min 95, max 95, sd 0, change over log +0 °C
Intake air: mean 56 °C, min 56, max 57, sd 0
Ambient: mean 27 °C, min 27, max 27, sd 0
Coolant stays in the normal warm band, thermostat and fan are regulating.

## Air flow and load
MAF: mean 3.84 g/s, min 3.72, max 3.94, sd 0.06
Calculated load: mean 25.0 %, min 24.3, max 25.5, sd 0.3, absolute load: mean 18.6 %, min 18.0, max 18.8, sd 0.2
Air per revolution 0.356 g, about 19 % volumetric efficiency for 3.2 L (15-30 % is normal at closed-throttle idle)
Throttle: absolute mean 19.1 %, min 18.8, max 19.2, sd 0.2; relative mean 4.5 %, min 4.3, max 4.7, sd 0.2; commanded actuator mean 6.7 %, min 6.7, max 7.1, sd 0.1

## Fuel trims and mixture
Bank 1: STFT mean -0.7 %, min -2.3, max 2.3, sd 1.2; LTFT mean 6.2 %, min 6.2, max 6.2, sd 0.0; total correction +5.6 %
Bank 2: STFT mean -0.4 %, min -3.1, max 2.3, sd 1.0; LTFT mean 9.4 %, min 9.4, max 9.4, sd 0.0; total correction +8.9 %
Commanded lambda: mean 1.000, min 1.000, max 1.000, sd 0.000
Evap purge duty: mean 0.0 %, min 0.0, max 0.0, sd 0.0

## Oxygen sensors
B1S1 (upstream): mean 0.407 V, min 0.075, max 0.810, sd 0.309, 4.2 rich/lean crossings per minute at this sample rate
B2S1 (upstream): mean 0.365 V, min 0.080, max 0.800, sd 0.292, 7.3 rich/lean crossings per minute at this sample rate
B1S2 (downstream): mean 0.655 V, min 0.630, max 0.705, sd 0.015
  B1S2 (downstream) is steady and rich-biased: catalyst is storing oxygen as expected.
B2S2 (downstream): mean 0.657 V, min 0.605, max 0.710, sd 0.025
  B2S2 (downstream) is steady and rich-biased: catalyst is storing oxygen as expected.
Note: with one sample every 3.8 s the upstream switching rate is under-sampled; a healthy sensor switches several times per second.

## Ignition, catalyst, electrical
Timing advance: mean 6.4 °, min 4.5, max 9.0, sd 1.0
Catalyst B1S1: mean 485 °C, min 485, max 485, sd 0; B2S1: mean 489 °C, min 489, max 489, sd 0
Module voltage: mean 13.85 V, min 13.76, max 13.95, sd 0.04
Charging system is within 13.0-15.0 V.

## Findings
No warning-level findings. The engine idles like a healthy, warm, closed-loop engine.
