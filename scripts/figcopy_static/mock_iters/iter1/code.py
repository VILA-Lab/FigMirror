# Drawer iter 1 — palette switched to Tableau-10; spines L+B only.
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(5, 3.2))
palette = ['#1f77b4', '#ff7f0e', '#2ca02c']
x = np.arange(0, 51, 5)
for i, c in enumerate(palette):
    y = 50 + 30 * (1 - np.exp(-x / 15)) - i * 4
    ax.plot(x, y, marker='o', color=c, ms=5, lw=1.5,
            label=f'method {chr(65+i)}')
ax.set_xlabel('epoch'); ax.set_ylabel('val accuracy (%)')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.legend(frameon=False)
fig.savefig('img_iter1.png')
