"""Concept-level proficiency model.

Each concept has an ability estimate theta on the same 1-10 scale as item difficulty
(theta = the difficulty at which the student has a 50% chance of success). After every
response, theta moves toward the evidence, Elo/1-PL-IRT style:

    p        = 1 / (1 + exp(-(theta - difficulty)))
    theta   += K * weight * (outcome - p)        K shrinks as evidence accumulates

A hard item answered correctly raises theta a lot; an easy item missed lowers it a lot.
Multi-concept items spread credit or blame by per-concept weights chosen by the engine.
"""

import math
from dataclasses import asdict, dataclass, field

MIN_DIFFICULTY, MAX_DIFFICULTY = 1, 10
DEFAULT_THETA = 4.0
# Thresholds relative to the difficulty scale (5 = on-grade, non-routine).
SECURE_THETA = 6.0
GAP_THETA = 4.0

LEVEL_DESCRIPTIONS = [
    (2.5, "Below grade level: still building the basics of this topic"),
    (4.5, "Approaching grade level: handles routine problems, needs support on multi-step ones"),
    (6.5, "At grade level: solid on routine and non-routine grade-level problems"),
    (8.5, "Above grade level: handles multi-step problems that combine concepts"),
    (99, "Well above grade level: ready for challenge and competition-style problems"),
]


def clamp_difficulty(x: float) -> int:
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, int(round(x))))


def p_correct(theta: float, difficulty: float) -> float:
    return 1.0 / (1.0 + math.exp(-(theta - difficulty)))


def outcome_value(is_correct: bool, timed_out: bool, attempt: int = 1) -> float:
    """Correct first time and in time = full credit. Correct only after a re-attempt, or over
    time, = partial credit (understanding not yet secure or fluent). Wrong = none."""
    if is_correct:
        return 0.5 if timed_out or attempt > 1 else 1.0
    return 0.0


def describe_level(theta: float) -> str:
    return next(text for limit, text in LEVEL_DESCRIPTIONS if theta < limit)


@dataclass
class ConceptEstimate:
    theta: float = DEFAULT_THETA
    evidence: float = 0.0          # weighted number of observations
    attempts: int = 0
    correct: int = 0
    last_outcome: float | None = None
    misconceptions: dict[str, int] = field(default_factory=dict)

    def update(self, difficulty: float, outcome: float, weight: float) -> float:
        k = max(0.6, 2.4 / (1.0 + 0.5 * self.evidence))
        delta = k * weight * (outcome - p_correct(self.theta, difficulty))
        self.theta = min(10.5, max(0.5, self.theta + delta))
        self.evidence += weight
        self.attempts += 1
        self.correct += int(outcome == 1.0)
        self.last_outcome = outcome
        return delta

    @property
    def mastery(self) -> str:
        if self.attempts == 0:
            return "not_assessed"
        if self.theta >= SECURE_THETA:
            return "secure"
        if self.theta < GAP_THETA and self.correct < self.attempts:
            return "gap"  # a low estimate alone isn't a gap; there must be a miss behind it
        return "developing"

    @property
    def confidence(self) -> str:
        if self.evidence < 1.5:
            return "low"
        return "medium" if self.evidence < 3.5 else "high"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(theta=round(self.theta, 2), evidence=round(self.evidence, 2),
                 mastery=self.mastery, confidence=self.confidence)
        return d

    @classmethod
    def from_prior(cls, theta: float, evidence: float) -> "ConceptEstimate":
        return cls(theta=theta, evidence=evidence)


class ProficiencyModel:
    """Per-session model: one estimate per concept plus an overall estimate."""

    def __init__(self, prior: dict[str, dict] | None = None, prior_overall: float = DEFAULT_THETA):
        self.concepts: dict[str, ConceptEstimate] = {
            cid: ConceptEstimate.from_prior(p["theta"], p["evidence"]) for cid, p in (prior or {}).items()
        }
        self.overall = ConceptEstimate(theta=prior_overall)
        self._default_theta = prior_overall

    def get(self, concept_id: str) -> ConceptEstimate:
        if concept_id not in self.concepts:
            self.concepts[concept_id] = ConceptEstimate(theta=self._default_theta)
        return self.concepts[concept_id]

    def theta_for(self, concept_id: str) -> float:
        """Best current guess. An untested concept with no remembered evidence borrows the
        student's overall ability, so strength (or weakness) shown elsewhere carries over."""
        est = self.get(concept_id)
        if est.attempts:
            return est.theta
        return (est.theta * est.evidence + self.overall.theta) / (est.evidence + 1.0)

    def apply(self, weights: dict[str, float], difficulty: float, outcome: float,
              misconceptions: list[tuple[str, str]] = ()) -> dict[str, float]:
        """Update every tagged concept once per problem; returns theta deltas per concept.
        misconceptions: (misconception, implicated concept id) for each wrong attempt."""
        for cid in weights:
            est = self.get(cid)
            if not est.attempts:
                est.theta = self.theta_for(cid)
        deltas = {cid: self.get(cid).update(difficulty, outcome, w) for cid, w in weights.items()}
        self.overall.update(difficulty, outcome, 1.0)
        for misconception, error_concept in misconceptions:
            target = error_concept if error_concept in weights else max(weights, key=weights.get)
            m = self.get(target).misconceptions
            m[misconception] = m.get(misconception, 0) + 1
        return deltas

    def snapshot(self) -> dict:
        return {
            "overall": self.overall.to_dict(),
            # Skip placeholders created by read-only lookups (no attempts, no prior evidence).
            "concepts": {cid: est.to_dict() for cid, est in self.concepts.items()
                         if est.attempts or est.evidence},
        }


def response_weights(primary: str, secondary: list[str], outcome: float, error_concept: str) -> dict[str, float]:
    """How much each tagged concept learns from this response.

    Success is evidence for every concept used. On a miss, blame goes mostly to the concept
    the grader attributed the error to (if any); otherwise mostly to the primary concept.
    """
    tagged = [primary, *[c for c in secondary if c != primary]]
    if outcome > 0 or not error_concept or error_concept not in tagged:
        return {cid: (1.0 if cid == primary else 0.5) for cid in tagged}
    return {cid: (1.0 if cid == error_concept else 0.25) for cid in tagged}
