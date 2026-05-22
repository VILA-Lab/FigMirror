# Drawer iter 0 — default-mpl pass.
# (Mock content — real CodexRunner / ClaudeRunner will emit
# the agent's actual generated script.)
import numpy as np
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(5, 3.2))
x = np.arange(0, 51, 5)
for i, c in enumerate(['red', 'green', 'blue']):
    y = 50 + 30 * (1 - np.exp(-x / 15)) - i * 4
    ax.plot(x, y, marker='o', color=c, label=f'method {chr(65+i)}')
ax.set_xlabel('epoch'); ax.set_ylabel('val accuracy (%)')
ax.legend()
fig.savefig('img_iter0.png')
