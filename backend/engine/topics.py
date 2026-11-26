"""Predefined K-12 math topics, grouped by strand, each with the grade range it suits.

Students can also type any new topic; those are saved to data/custom_topics.jsonl and
offered alongside the predefined list from then on.
"""

# (topic, first grade, last grade); grade 0 = Kindergarten
PREDEFINED: dict[str, list[tuple[str, int, int]]] = {
    "Number & Operations": [
        ("Counting and cardinality", 0, 1),
        ("Addition and subtraction", 0, 3),
        ("Place value", 1, 4),
        ("Multiplication and division", 3, 5),
        ("Factors, multiples and primes", 4, 6),
        ("Fractions", 3, 6),
        ("Decimals", 4, 6),
        ("Percentages", 6, 8),
        ("Ratios and rates", 6, 7),
        ("Proportional relationships", 7, 8),
        ("Integers and negative numbers", 6, 7),
        ("Operations with rational numbers", 7, 8),
        ("Exponents and scientific notation", 8, 9),
        ("Square roots and irrational numbers", 8, 9),
    ],
    "Algebra": [
        ("Patterns and sequences", 2, 5),
        ("Numerical expressions and order of operations", 5, 6),
        ("Algebraic expressions", 6, 8),
        ("One-variable equations", 6, 8),
        ("Inequalities", 6, 9),
        ("Linear equations", 7, 9),
        ("Linear functions and slope", 8, 10),
        ("Systems of linear equations", 8, 10),
        ("Functions", 8, 11),
        ("Polynomials", 9, 11),
        ("Factoring", 9, 11),
        ("Quadratic equations", 9, 11),
        ("Exponential functions", 9, 12),
        ("Sequences and series", 10, 12),
        ("Logarithms", 11, 12),
    ],
    "Geometry": [
        ("Shapes and their attributes", 0, 3),
        ("Area and perimeter", 3, 6),
        ("Angles", 4, 7),
        ("Coordinate plane", 5, 8),
        ("Volume and surface area", 5, 8),
        ("Circles", 7, 10),
        ("Transformations", 8, 10),
        ("Similarity and congruence", 8, 10),
        ("Triangles and the Pythagorean theorem", 8, 10),
        ("Trigonometry", 9, 12),
    ],
    "Measurement & Data": [
        ("Time and money", 1, 3),
        ("Measurement and unit conversion", 2, 5),
        ("Data and graphs", 2, 6),
        ("Mean, median, mode and range", 6, 8),
        ("Probability", 7, 12),
        ("Statistics and data analysis", 9, 12),
    ],
    "Calculus": [
        ("Limits", 11, 12),
        ("Derivatives", 11, 12),
        ("Integrals", 12, 12),
    ],
}


def grade_number(grade: str) -> int | None:
    g = grade.strip().lower()
    if g in ("k", "kindergarten"):
        return 0
    return int(g) if g.isdigit() else None


def predefined_for(grade: str | None) -> list[dict]:
    """Strands with the topics that suit `grade` (all topics when grade is None)."""
    g = grade_number(grade) if grade else None
    out = []
    for strand, topics in PREDEFINED.items():
        names = [name for name, lo, hi in topics if g is None or lo <= g <= hi]
        if names:
            out.append({"strand": strand, "topics": names})
    return out


def is_predefined(topic: str) -> bool:
    t = topic.strip().lower()
    return any(t == name.lower() for topics in PREDEFINED.values() for name, _, _ in topics)
