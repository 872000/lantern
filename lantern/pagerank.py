"""PageRank via power iteration, implemented from scratch with numpy.

Algorithm
---------
* Build the column-stochastic transition matrix ``A`` from ``links``:
  ``A[dst, src] = 1 / outdegree(src)`` for each link ``src -> dst``.
* Dangling nodes (no outlinks) get a uniform column ``1/n`` -- their
  rank mass is redistributed evenly instead of leaking out of the
  system.
* Iterate ``r <- d * A @ r + (1 - d) / n`` from the uniform vector
  until the L1 change drops below ``tol`` (default 1e-6) or
  ``max_iter`` (default 100) iterations are done.
* Scores are written back into ``docs.pagerank``.

No sklearn, no networkx -- just numpy for the matrix-vector product.
"""

from __future__ import annotations

import sqlite3

from .index import _resolve


def compute_pagerank(db="lantern.db", damping=0.85, tol=1e-6, max_iter=100):
    """Compute PageRank for every doc in ``lantern.db``.

    Returns ``{doc_id: score}`` with scores summing to 1. Also persists
    the scores into ``docs.pagerank``.
    """
    import numpy as np

    db = _resolve(db)
    con = sqlite3.connect(str(db))
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM docs ORDER BY id")]
        links = list(con.execute("SELECT src, dst FROM links"))
    finally:
        con.close()

    n = len(ids)
    if n == 0:
        return {}

    pos = {d: i for i, d in enumerate(ids)}
    A = np.zeros((n, n))
    outdeg = np.zeros(n)
    for s, d in links:
        if s in pos and d in pos:
            A[pos[d], pos[s]] += 1.0
            outdeg[pos[s]] += 1.0
    for i in range(n):
        if outdeg[i] > 0:
            A[:, i] /= outdeg[i]
        else:
            # Dangling node: redistribute its mass uniformly.
            A[:, i] = 1.0 / n

    r = np.full(n, 1.0 / n)
    teleport = (1.0 - damping) / n
    for _ in range(max_iter):
        r_new = damping * (A @ r) + teleport
        if np.abs(r_new - r).sum() < tol:
            r = r_new
            break
        r = r_new
    r = r / r.sum()  # guard against float drift

    scores = {d: float(r[pos[d]]) for d in ids}
    con = sqlite3.connect(str(db))
    try:
        con.executemany("UPDATE docs SET pagerank = ? WHERE id = ?",
                        [(scores[d], d) for d in ids])
        con.commit()
    finally:
        con.close()
    return scores
