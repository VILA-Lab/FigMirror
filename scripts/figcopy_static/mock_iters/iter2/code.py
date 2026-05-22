# Drawer iter 2 — tick density via MaxNLocator(6).
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# (palette + spines as iter 1; tick locator added)
ax.xaxis.set_major_locator(MaxNLocator(6))
