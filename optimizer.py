"""Team Trials team builder.

A Team Trials roster is 15 umas: three in each of five race categories
(Sprint, Mile, Medium, Long, Dirt). Each uma runs in exactly one slot, and
each of the three umas in a category is given a running style. Running the
same style three times in one category makes them fight each other for
position, so the three styles should differ.

That is an assignment problem, and it is solved here exactly rather than
greedily. The graph is:

    source -> uma            capacity 1   (each uma used at most once)
    uma    -> (category, style)  capacity 1, cost = -score
    (category, style) -> category   three parallel arcs, capacity 1 each,
                                    costs 0 / penalty / 2*penalty
    category -> sink         capacity 3   (three umas per category)

Because each (category, style) node reaches its category through arcs that
get progressively more expensive, the first uma of a given style in a
category is free, the second costs `style_penalty`, the third costs twice
that. Set the penalty high enough and distinct styles become effectively
mandatory; set it to zero and style is ignored. Min-cost flow of value 15
over that graph is the best possible team under the scoring model.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

# --- game constants -------------------------------------------------------
# Aptitude grades scale a runner's effective speed (distance/surface) and
# acceleration (style). These are the community-documented multipliers; they
# live here so a single edit fixes every score if the game rebalances them.
GRADES = ("S", "A", "B", "C", "D", "E", "F", "G")

DISTANCE_MULT = {"S": 1.05, "A": 1.00, "B": 0.90, "C": 0.80, "D": 0.60, "E": 0.40, "F": 0.20, "G": 0.10}
SURFACE_MULT = dict(DISTANCE_MULT)
STYLE_MULT = {"S": 1.10, "A": 1.00, "B": 0.85, "C": 0.75, "D": 0.60, "E": 0.40, "F": 0.20, "G": 0.10}

STYLES = ("front", "pace", "late", "end")
STYLE_LABELS = {"front": "Front Runner", "pace": "Pace Chaser", "late": "Late Surger", "end": "End Closer"}

STATS = ("speed", "stamina", "power", "guts", "wit")

# Tier from the tracentrial.org Team Trials tierlist. Cards it has not rated
# score as UNRATED_POINTS -- treated as "unknown", not "bad", because the
# tierlist only covers cards already released on Global.
TIER_POINTS = {"S": 100.0, "A": 75.0, "B": 50.0, "C": 25.0}
UNRATED_POINTS = 40.0


@dataclass(frozen=True)
class Category:
    """One of the five Team Trials race categories."""

    key: str
    label: str
    surface: str
    distances: tuple[str, ...]
    # Rough weighting of which stats carry a race at this distance. Used only
    # for the small stat-fit term, so precision here matters little.
    stat_weights: dict[str, float]


DEFAULT_CATEGORIES: tuple[Category, ...] = (
    Category("sprint", "Sprint", "turf", ("sprint",),
             {"speed": 0.35, "stamina": 0.05, "power": 0.30, "guts": 0.15, "wit": 0.15}),
    Category("mile", "Mile", "turf", ("mile",),
             {"speed": 0.35, "stamina": 0.15, "power": 0.25, "guts": 0.10, "wit": 0.15}),
    Category("medium", "Medium", "turf", ("medium",),
             {"speed": 0.30, "stamina": 0.25, "power": 0.20, "guts": 0.10, "wit": 0.15}),
    Category("long", "Long", "turf", ("long",),
             {"speed": 0.25, "stamina": 0.35, "power": 0.18, "guts": 0.07, "wit": 0.15}),
    # Dirt is a surface category, not a distance one -- its races rotate over
    # several distances, so it averages the aptitudes that apply.
    Category("dirt", "Dirt", "dirt", ("mile", "medium"),
             {"speed": 0.30, "stamina": 0.15, "power": 0.30, "guts": 0.10, "wit": 0.15}),
)


@dataclass
class Weights:
    """How much each term contributes to an uma's score for a slot."""

    aptitude: float = 1.0   # can she physically run this race
    tier: float = 0.5       # what the tierlist thinks of the card
    stats: float = 0.15     # stat caps and growth bonuses
    style_penalty: float = 120.0  # cost of reusing a style inside one category


@dataclass
class CardView:
    """An uma as the optimizer sees her: card data plus any raised aptitudes."""

    card_id: int
    name: str
    tier: str | None
    tier_note: str | None
    image_url: str | None
    aptitude: dict[str, str]
    max_stats: dict[str, int]
    growth: dict[str, int]

    def grade(self, key: str) -> str:
        return self.aptitude.get(key, "G")


@dataclass
class Assignment:
    card_id: int
    name: str
    category: str
    category_label: str
    style: str
    style_label: str
    style_grade: str
    surface_grade: str
    distance_grades: dict[str, str]
    tier: str | None
    score: float
    breakdown: dict[str, float]
    duplicate_style: bool = False


@dataclass
class TeamResult:
    assignments: list[Assignment] = field(default_factory=list)
    total_score: float = 0.0
    unfilled: list[str] = field(default_factory=list)

    def by_category(self) -> dict[str, list[Assignment]]:
        out: dict[str, list[Assignment]] = {}
        for a in self.assignments:
            out.setdefault(a.category, []).append(a)
        for group in out.values():
            group.sort(key=lambda a: -a.score)
        return out

    @property
    def style_conflicts(self) -> int:
        """Number of umas sharing a style with another uma in their category."""
        return sum(1 for a in self.assignments if a.duplicate_style)


# --- scoring --------------------------------------------------------------

def aptitude_fit(card: CardView, cat: Category, style: str) -> tuple[float, dict[str, float]]:
    surface = SURFACE_MULT.get(card.grade(cat.surface), 0.1)
    dist_mults = [DISTANCE_MULT.get(card.grade(d), 0.1) for d in cat.distances]
    distance = sum(dist_mults) / len(dist_mults)
    style_m = STYLE_MULT.get(card.grade(style), 0.1)
    return surface * distance * style_m, {
        "surface": surface,
        "distance": distance,
        "style": style_m,
    }


def stat_points(card: CardView, cat: Category) -> float:
    """0-100ish: stat caps weighted for the distance, nudged by growth bonuses."""
    cap = sum(cat.stat_weights[s] * (card.max_stats.get(s) or 0) for s in STATS)
    growth = sum(cat.stat_weights[s] * (card.growth.get(s) or 0) for s in STATS)
    # Five-star caps sit near 100-120 per stat, so the weighted sum is already
    # on roughly a 0-120 scale. Growth is a percentage; 2x makes it comparable.
    return cap + growth * 2.0


def score_card(card: CardView, cat: Category, style: str, w: Weights) -> tuple[float, dict[str, float]]:
    fit, parts = aptitude_fit(card, cat, style)
    tier = TIER_POINTS.get(card.tier or "", UNRATED_POINTS)
    stats = stat_points(card, cat)

    apt_term = w.aptitude * 100.0 * fit
    tier_term = w.tier * tier
    stat_term = w.stats * stats
    total = apt_term + tier_term + stat_term
    return total, {
        "aptitude": apt_term,
        "tier": tier_term,
        "stats": stat_term,
        "fit": fit,
        **parts,
    }


# --- min-cost max-flow ----------------------------------------------------

class _MCMF:
    """Successive shortest paths with Johnson potentials. Costs must be >= 0."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.graph: list[list[list]] = [[] for _ in range(n)]

    def add(self, u: int, v: int, cap: int, cost: float) -> None:
        self.graph[u].append([v, cap, cost, len(self.graph[v])])
        self.graph[v].append([u, 0, -cost, len(self.graph[u]) - 1])

    def flow(self, s: int, t: int, want: int) -> tuple[int, float]:
        n, graph = self.n, self.graph
        potential = [0.0] * n
        total_cost = 0.0
        sent = 0

        while sent < want:
            dist = [float("inf")] * n
            dist[s] = 0.0
            prev_v = [-1] * n
            prev_e = [-1] * n
            heap = [(0.0, s)]
            while heap:
                d, u = heapq.heappop(heap)
                if d > dist[u] + 1e-12:
                    continue
                for i, (v, cap, cost, _rev) in enumerate(graph[u]):
                    if cap <= 0:
                        continue
                    nd = d + cost + potential[u] - potential[v]
                    if nd < dist[v] - 1e-12:
                        dist[v] = nd
                        prev_v[v] = u
                        prev_e[v] = i
                        heapq.heappush(heap, (nd, v))
            if dist[t] == float("inf"):
                break  # no augmenting path: fewer umas than slots

            for i in range(n):
                if dist[i] < float("inf"):
                    potential[i] += dist[i]

            # Every path here carries one unit (source arcs have capacity 1).
            push = want - sent
            v = t
            while v != s:
                push = min(push, graph[prev_v[v]][prev_e[v]][1])
                v = prev_v[v]
            v = t
            while v != s:
                edge = graph[prev_v[v]][prev_e[v]]
                edge[1] -= push
                graph[v][edge[3]][1] += push
                total_cost += push * edge[2]
                v = prev_v[v]
            sent += push

        return sent, total_cost


# --- the solver -----------------------------------------------------------

def optimize(
    cards: list[CardView],
    weights: Weights | None = None,
    categories: tuple[Category, ...] = DEFAULT_CATEGORIES,
    allowed_styles: dict[int, set[str]] | None = None,
    per_category: int = 3,
) -> TeamResult:
    """Best assignment of `cards` to the Team Trials slots.

    `allowed_styles` optionally restricts an uma to styles you are willing to
    run her as, keyed by card_id. Anything absent may use all four.
    """
    w = weights or Weights()
    slots_total = per_category * len(categories)
    if not cards:
        return TeamResult(unfilled=[c.label for c in categories for _ in range(per_category)])

    n_cards = len(cards)
    n_cat = len(categories)
    # Node layout: 0 source | cards | (category, style) | category | sink
    src = 0
    card_base = 1
    slot_base = card_base + n_cards
    cat_base = slot_base + n_cat * len(STYLES)
    sink = cat_base + n_cat
    mcmf = _MCMF(sink + 1)

    # Costs must be non-negative for Dijkstra. Because the flow value is fixed
    # at 15, adding a constant to every card->slot arc shifts the total by a
    # constant and leaves the optimal assignment unchanged.
    scored: dict[tuple[int, int, str], tuple[float, dict[str, float]]] = {}
    best = 0.0
    for ci, card in enumerate(cards):
        for gi, cat in enumerate(categories):
            for style in STYLES:
                total, parts = score_card(card, cat, style, w)
                scored[(ci, gi, style)] = (total, parts)
                best = max(best, total)

    for ci in range(n_cards):
        mcmf.add(src, card_base + ci, 1, 0.0)

    for gi in range(n_cat):
        for si, style in enumerate(STYLES):
            slot = slot_base + gi * len(STYLES) + si
            for ci, card in enumerate(cards):
                if allowed_styles is not None:
                    permitted = allowed_styles.get(card.card_id)
                    if permitted is not None and style not in permitted:
                        continue
                total, _ = scored[(ci, gi, style)]
                mcmf.add(card_base + ci, slot, 1, best - total)
            # First use of this style in the category is free; reuse is taxed.
            for k in range(per_category):
                mcmf.add(slot, cat_base + gi, 1, w.style_penalty * k)
        mcmf.add(cat_base + gi, sink, per_category, 0.0)

    sent, _cost = mcmf.flow(src, sink, slots_total)

    # Read the chosen assignments back off the saturated card->slot arcs.
    result = TeamResult()
    used_styles: dict[int, list[str]] = {}
    for ci, card in enumerate(cards):
        for edge in mcmf.graph[card_base + ci]:
            v, cap, _cost, _rev = edge
            if cap != 0 or not (slot_base <= v < cat_base):
                continue
            offset = v - slot_base
            gi, si = divmod(offset, len(STYLES))
            cat = categories[gi]
            style = STYLES[si]
            total, parts = scored[(ci, gi, style)]
            result.assignments.append(
                Assignment(
                    card_id=card.card_id,
                    name=card.name,
                    category=cat.key,
                    category_label=cat.label,
                    style=style,
                    style_label=STYLE_LABELS[style],
                    style_grade=card.grade(style),
                    surface_grade=card.grade(cat.surface),
                    distance_grades={d: card.grade(d) for d in cat.distances},
                    tier=card.tier,
                    score=total,
                    breakdown=parts,
                )
            )
            used_styles.setdefault(gi, []).append(style)
            break

    for a in result.assignments:
        gi = next(i for i, c in enumerate(categories) if c.key == a.category)
        a.duplicate_style = used_styles[gi].count(a.style) > 1

    result.total_score = sum(a.score for a in result.assignments)
    if sent < slots_total:
        filled = {c.key: 0 for c in categories}
        for a in result.assignments:
            filled[a.category] += 1
        result.unfilled = [
            c.label for c in categories for _ in range(per_category - filled[c.key])
        ]
    return result
