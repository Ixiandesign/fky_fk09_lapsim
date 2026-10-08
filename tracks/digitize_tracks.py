"""One-time script: trace the red driving line in the track images and save x,y centerlines (metres).

Run with:  uv run python tracks/digitize_tracks.py
Output:    tracks/autocross.csv, tracks/endurance.csv  (columns x_m, y_m, in driving order)

The images are low resolution (about 0.7 m per pixel), so these tracks are approximate.
Replace the CSVs with surveyed / GPS data when you have it.
"""
from collections import deque

import numpy as np
import matplotlib.image as mpimg
import matplotlib.pyplot as plt

FT = 0.3048

# metres per pixel, measured from the "0'" and "1700'" / "2000'" labels on the grid
IMAGES = {
    "autocross": dict(file="ref/track images/autox track.png", m_per_px=(1700 * FT) / (978.5 - 203.0), closed=False, start=(328, 55), thr=0.3, grow=1),
    "endurance": dict(file="ref/track images/endurance track.png", m_per_px=(2000 * FT) / (886.3 - 91.1), closed=True, start=(600, 52), thr=0.12, grow=2),
}


def red_pixels(img, thr):
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    # autocross line is bright red, endurance line is pale pink, hence a different threshold each
    return (r > 0.85) & (r - g > thr) & (r - b > thr)


def order_pixels(mask, start, closed, grow):
    """Order the red pixels along the line.

    Breadth-first search from the start pixel gives every pixel its distance along the line.
    Pixels at the same distance sit across the line width, so averaging them gives the centerline.
    """
    mask = mask.copy()
    # close 1-pixel gaps in the drawn line so the search can walk through them
    grown = mask.copy()
    for dy in range(-grow, grow + 1):
        for dx in range(-grow, grow + 1):
            grown |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    sx, sy = start
    if closed:  # cut the loop open at the start line so distance grows all the way round
        yy, xx = np.ogrid[: mask.shape[0], : mask.shape[1]]
        grown[(xx - sx) ** 2 + (yy - sy) ** 2 < 6**2] = False
        sx += 8  # begin just after the cut, going in the direction of travel
    ys, xs = np.nonzero(grown)
    k = np.argmin((xs - sx) ** 2 + (ys - sy) ** 2)
    dist = {(xs[k], ys[k]): 0}
    queue = deque([(xs[k], ys[k])])
    while queue:
        x, y = queue.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nxt = (x + dx, y + dy)
                if nxt not in dist and 0 <= nxt[1] < grown.shape[0] and 0 <= nxt[0] < grown.shape[1] and grown[nxt[1], nxt[0]]:
                    dist[nxt] = dist[(x, y)] + 1
                    queue.append(nxt)
    pts = np.array(list(dist.keys()), dtype=float)
    d = np.array(list(dist.values()))
    bins = d // 4
    centers = np.array([pts[bins == b].mean(axis=0) for b in np.unique(bins)])
    return centers


def smooth(a, n, closed):
    """Moving average of a 1-D array with window n (wraps around if the track is closed)."""
    kernel = np.ones(n) / n
    if closed:
        padded = np.concatenate([a[-n:], a, a[:n]])
        return np.convolve(padded, kernel, mode="same")[n:-n]
    padded = np.concatenate([np.full(n, a[0]), a, np.full(n, a[-1])])
    return np.convolve(padded, kernel, mode="same")[n:-n]


def trace(name, file, m_per_px, closed, start, thr, grow):
    img = mpimg.imread(file)[..., :3]
    block = order_pixels(red_pixels(img, thr), start, closed, grow)
    x = smooth(block[:, 0], 5, closed) * m_per_px
    y = -smooth(block[:, 1], 5, closed) * m_per_px  # image y points down
    out = np.column_stack([x, y])
    np.savetxt(f"tracks/{name}.csv", out, delimiter=",", header="x_m,y_m", comments="", fmt="%.2f")
    length = np.sum(np.hypot(np.diff(x), np.diff(y)))
    print(f"{name}: {len(out)} points, {length:.0f} m long")
    return img, out


if __name__ == "__main__":
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    for ax, (name, cfg) in zip(axes, IMAGES.items()):
        img, out = trace(name, **cfg)
        ax.imshow(img)
        ax.plot(out[:, 0] / cfg["m_per_px"], -out[:, 1] / cfg["m_per_px"], "b.", ms=2)
        ax.plot(out[0, 0] / cfg["m_per_px"], -out[0, 1] / cfg["m_per_px"], "go")
        ax.set_title(name)
    plt.savefig("tracks/digitize_check.png", dpi=110)
