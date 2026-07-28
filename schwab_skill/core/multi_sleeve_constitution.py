"""Multi-sleeve constitution constants (locked 2026-07-27 grilling).

See wiki/multi-sleeve-trading-system-constitution.md.
"""

from __future__ import annotations

# Sleeve IDs
SLEEVE_S0 = "S0"
SLEEVE_S1 = "S1"
SLEEVE_S3 = "S3"
SLEEVE_S5 = "S5"
SLEEVE_CASH = SLEEVE_S3

# Capital caps (fractions of equity)
CAP_S0 = 0.80
CAP_S1 = 0.15
CAP_S1_PROMOTED = 0.25
CAP_GROSS = 1.00
CAP_NAME = 0.08
CAP_CLUSTER = 0.25

# Cardinality
TOP_N_S0 = 5
TOP_N_S1 = 3

# Rebalance
HYSTERESIS_ABS_WEIGHT = 0.01

# Drawdown staircase (fraction of peak equity)
DD_SOFT = 0.12
DD_HARD = 0.18
DD_FLOOR = 0.20

# Crash mode
CRASH_SPY_DROP = 0.07
CRASH_LOOKBACK_DAYS = 10
CRASH_CLEAR_SMA = 20
CRASH_CLEAR_NO_NEW_LOW_DAYS = 3

# Pareto floors
PF_MEAN_FLOOR = 1.20
PF_WORST_ERA_FLOOR = 1.00

# R7
R7_MIN_N = 40
R7_PRIMARY_HORIZON = "5"
R7_SLEEVES_DEFAULT = (SLEEVE_S0, SLEEVE_S1)

# Research chip IDs (wave 1 order)
RESEARCH_WAVE = ("R7", "R3", "R2", "R4", "R6", "R1", "R5")
