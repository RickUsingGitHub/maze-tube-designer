"""Maze generation on a cylinder.

The maze lives on a grid of ``rows`` x ``cols`` cells that wraps around
horizontally (the circumference of the tube).  Row 0 is the bottom of the
maze (next to the base of the body, where the cap is fully closed) and row
``rows - 1`` is the top, where the exit leads out over the rim.

Only one *sector* is generated.  When the tube has several nubs, the sector
is repeated around the circumference so that every nub sees an identical
maze and they all travel in lock-step.

Passages are stored as two boolean arrays:

* ``h_open[r, c]`` - passage between cell (r, c) and (r, (c + 1) % cols)
* ``v_open[r, c]`` - passage between cell (r, c) and (r + 1, c)

Only ``random.Random.random()`` is used so that a seed reproduces the same
maze on any Python version.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

import numpy as np

ALGORITHMS = {
    "backtracker": "Recursive backtracker",
    "prim": "Prim",
    "kruskal": "Kruskal",
    "wilson": "Wilson",
}

ALGORITHM_TIPS = {
    "backtracker": "Long winding corridors with few dead ends - long solutions.",
    "prim": "Many short dead ends branching off everywhere.",
    "kruskal": "Balanced mix of corridors and branches.",
    "wilson": "Unbiased (uniform) maze - every possible maze equally likely.",
}


@dataclass
class Maze:
    rows: int
    cols: int
    h_open: np.ndarray
    v_open: np.ndarray
    home: int = 0           # column of the home (closed) cell in row 0
    entry: int = 0          # column of the exit cell in the top row
    solution: list = field(default_factory=list)   # [(r, c), ...] home -> exit
    moves: list = field(default_factory=list)      # [(dc, dr), ...] per step

    # ------------------------------------------------------------------ graph
    def neighbours(self, r: int, c: int):
        """Yield (nr, nc, dc, dr) for every open passage out of (r, c)."""
        cols = self.cols
        if cols > 1:
            if self.h_open[r, c]:
                yield r, (c + 1) % cols, +1, 0
            if self.h_open[r, (c - 1) % cols]:
                yield r, (c - 1) % cols, -1, 0
        if r + 1 < self.rows and self.v_open[r, c]:
            yield r + 1, c, 0, +1
        if r > 0 and self.v_open[r - 1, c]:
            yield r - 1, c, 0, -1

    def bfs(self, start):
        """Breadth-first search; returns (dist array, parent dict)."""
        dist = np.full((self.rows, self.cols), -1, dtype=int)
        parent = {}
        sr, sc = start
        dist[sr, sc] = 0
        q = deque([start])
        while q:
            r, c = q.popleft()
            for nr, nc, dc, dr in self.neighbours(r, c):
                if dist[nr, nc] < 0:
                    dist[nr, nc] = dist[r, c] + 1
                    parent[(nr, nc)] = (r, c, dc, dr)
                    q.append((nr, nc))
        return dist, parent

    def solve(self):
        """Compute the path from home to exit (fills solution & moves)."""
        _, parent = self.bfs((0, self.home))
        path, moves = [], []
        cur = (self.rows - 1, self.entry)
        if cur != (0, self.home) and cur not in parent:
            self.solution, self.moves = [], []
            return
        while cur != (0, self.home):
            r, c, dc, dr = parent[cur]
            path.append(cur)
            moves.append((dc, dr))
            cur = (r, c)
        path.append(cur)
        self.solution = path[::-1]
        self.moves = moves[::-1]

    # ------------------------------------------------------------- statistics
    def dead_ends(self) -> int:
        count = 0
        for r in range(self.rows):
            for c in range(self.cols):
                if sum(1 for _ in self.neighbours(r, c)) == 1:
                    count += 1
        return count

    def turns(self) -> int:
        """Number of direction changes along the solution."""
        t = 0
        for a, b in zip(self.moves, self.moves[1:]):
            if a != b:
                t += 1
        return t

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "h_open": "".join("1" if x else "0" for x in self.h_open.ravel()),
            "v_open": "".join("1" if x else "0" for x in self.v_open.ravel()),
            "home": self.home,
            "entry": self.entry,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Maze":
        rows, cols = int(d["rows"]), int(d["cols"])
        h = np.array([ch == "1" for ch in d["h_open"]], dtype=bool).reshape(rows, cols)
        v = np.array([ch == "1" for ch in d["v_open"]], dtype=bool)
        v = v.reshape(max(rows - 1, 0), cols)
        m = cls(rows, cols, h, v, int(d["home"]), int(d["entry"]))
        m.solve()
        return m


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def _edges(rows: int, cols: int):
    """List of edges as (kind, r, c, cell_a, cell_b)."""
    edges = []
    if cols > 1:
        for r in range(rows):
            for c in range(cols):
                edges.append(("h", r, c, (r, c), (r, (c + 1) % cols)))
    for r in range(rows - 1):
        for c in range(cols):
            edges.append(("v", r, c, (r, c), (r + 1, c)))
    return edges


def _weighted_pick(rng: random.Random, items, weights):
    total = sum(weights)
    x = rng.random() * total
    acc = 0.0
    for item, w in zip(items, weights):
        acc += w
        if x < acc:
            return item
    return items[-1]


def generate(rows: int, cols: int, algorithm: str = "backtracker", seed: int = 1,
             horizontal_bias: float = 1.0, loops: float = 0.0,
             longest: bool = True) -> Maze:
    """Generate a perfect maze (plus optional loops) on a wrapped grid."""
    if rows < 1 or cols < 1:
        raise ValueError("maze needs at least one row and one column")
    rng = random.Random(seed)
    hb = max(0.05, float(horizontal_bias))
    edges = _edges(rows, cols)
    weight = {e[:3]: (hb if e[0] == "h" else 1.0) for e in edges}
    adj = {(r, c): [] for r in range(rows) for c in range(cols)}
    for e in edges:
        _, _, _, a, b = e
        adj[a].append((e, b))
        adj[b].append((e, a))

    open_edges = set()

    if algorithm == "prim":
        start = (int(rng.random() * rows), int(rng.random() * cols))
        visited = {start}
        frontier = list(adj[start])
        while frontier:
            item = _weighted_pick(rng, frontier, [weight[e[:3]] for e, _ in frontier])
            frontier.remove(item)
            e, n = item
            if n in visited:
                continue
            visited.add(n)
            open_edges.add(e[:3])
            frontier.extend((e2, n2) for e2, n2 in adj[n] if n2 not in visited)

    elif algorithm == "kruskal":
        parent = {cell: cell for cell in adj}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Efraimidis-Spirakis weighted random permutation
        keyed = sorted(edges, key=lambda e: -(rng.random() ** (1.0 / weight[e[:3]])))
        for e in keyed:
            ra, rb = find(e[3]), find(e[4])
            if ra != rb:
                parent[ra] = rb
                open_edges.add(e[:3])

    elif algorithm == "wilson":
        cells = list(adj)
        in_tree = {cells[int(rng.random() * len(cells))]}
        remaining = [c for c in cells if c not in in_tree]
        order = sorted(remaining, key=lambda _: rng.random())
        for start in order:
            if start in in_tree:
                continue
            # loop-erased random walk
            walk_next = {}
            cur = start
            while cur not in in_tree:
                opts = adj[cur]
                e, n = _weighted_pick(rng, opts, [weight[x[0][:3]] for x in opts])
                walk_next[cur] = (e, n)
                cur = n
            cur = start
            while cur not in in_tree:
                e, n = walk_next[cur]
                open_edges.add(e[:3])
                in_tree.add(cur)
                cur = n

    else:  # recursive backtracker (iterative DFS)
        start = (int(rng.random() * rows), int(rng.random() * cols))
        visited = {start}
        stack = [start]
        while stack:
            cur = stack[-1]
            opts = [(e, n) for e, n in adj[cur] if n not in visited]
            if not opts:
                stack.pop()
                continue
            e, n = _weighted_pick(rng, opts, [weight[x[0][:3]] for x in opts])
            visited.add(n)
            open_edges.add(e[:3])
            stack.append(n)

    # optional loops: knock out some extra walls
    if loops > 0:
        closed = [e[:3] for e in edges if e[:3] not in open_edges]
        n_extra = int(round(len(closed) * min(loops, 100.0) / 100.0))
        closed.sort(key=lambda _: rng.random())
        open_edges.update(closed[:n_extra])

    h_open = np.zeros((rows, cols), dtype=bool)
    v_open = np.zeros((max(rows - 1, 0), cols), dtype=bool)
    for kind, r, c in open_edges:
        if kind == "h":
            h_open[r, c] = True
        else:
            v_open[r, c] = True

    maze = Maze(rows, cols, h_open, v_open)
    _place_home_and_exit(maze, rng, longest)
    maze.solve()
    return maze


def _place_home_and_exit(maze: Maze, rng: random.Random, longest: bool):
    top = maze.rows - 1
    if longest:
        best = None
        for home in range(maze.cols):
            dist, _ = maze.bfs((0, home))
            row = dist[top]
            for entry in range(maze.cols):
                d = row[entry]
                key = (d, rng.random())
                if best is None or key > best[0]:
                    best = (key, home, entry)
        maze.home, maze.entry = best[1], best[2]
    else:
        maze.home = int(rng.random() * maze.cols)
        maze.entry = int(rng.random() * maze.cols)
