"""Composable SQL fragments for filtering the question pool.

Every fragment assumes the questions table is aliased `q` and returns its own bind
parameters, prefixed so that several fragments can be combined in one statement.
"""

from __future__ import annotations

from typing import Any

# A per-user correction "counts" when it carries an option, an explanation or a
# structured answer. Expects `question_corrections` aliased as `qc`.
HAS_CORRECTION_SQL = """(
  qc.correct_option_id IS NOT NULL
  OR NULLIF(BTRIM(qc.explanation_text), '') IS NOT NULL
  OR qc.answer_payload <> '{}'::jsonb
)"""

SqlFragment = tuple[str, dict[str, Any]]


def parse_tag_values(raw: str | None, *, limit: int = 30) -> list[str]:
    """Split the UI tag input ("reti, tcp-ip, routing") into distinct values (OR semantics)."""
    if not raw:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        part = part.strip()
        # Be forgiving: users sometimes paste quoted CSV-like strings.
        if part and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1].strip()
        if not part or part in seen:
            continue
        values.append(part)
        seen.add(part)
        if len(values) >= limit:
            break
    return values


def _matches_any(column_prefix: str, values: list[str], param_prefix: str) -> SqlFragment:
    """`(x.slug = :p0 OR x.name = :p0 OR x.id::text = :p0 OR ...)` for each value."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for idx, value in enumerate(values):
        key = f"{param_prefix}_{idx}"
        clauses.append(
            f"({column_prefix}.slug = :{key} OR {column_prefix}.name = :{key} "
            f"OR CAST({column_prefix}.id AS TEXT) = :{key})"
        )
        params[key] = value
    return " OR ".join(clauses), params


def any_tag(values: list[str], *, param_prefix: str = "tag") -> SqlFragment | None:
    """Question has at least one of the given tags (matched by slug, name or id)."""
    if not values:
        return None
    match_sql, params = _matches_any("t", values, param_prefix)
    sql = f"""EXISTS (
      SELECT 1 FROM question_tags qt
      JOIN tags t ON t.id = qt.tag_id
      WHERE qt.question_id = q.id AND ({match_sql})
    )"""
    return sql, params


def any_tag_preset(values: list[str], *, param_prefix: str = "preset") -> SqlFragment | None:
    """Question has a tag that belongs to at least one of the given presets."""
    if not values:
        return None
    match_sql, params = _matches_any("tp", values, param_prefix)
    sql = f"""EXISTS (
      SELECT 1 FROM question_tags qt2
      JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
      JOIN tag_presets tp ON tp.id = ppt.preset_id
      WHERE qt2.question_id = q.id AND ({match_sql})
    )"""
    return sql, params


def appears_in_documents(param: str) -> str:
    """Question occurs in one of the documents bound to `param` (a list of ids).

    After deduplication a question is owned by a single document but can occur in
    several, so filters by document must look at occurrences, not ownership.
    """
    return f"""EXISTS (
      SELECT 1 FROM question_occurrences occ
      WHERE occ.question_id = q.id AND occ.document_id = ANY(CAST(:{param} AS UUID[]))
    )"""


def has_correction(*, user_param: str, negate: bool = False) -> str:
    """Question has (or, with `negate`, lacks) a usable correction for the user bound to `user_param`."""
    sql = f"""EXISTS (
      SELECT 1 FROM question_corrections qc
      WHERE qc.question_id = q.id AND qc.user_id = :{user_param} AND {HAS_CORRECTION_SQL}
    )"""
    return f"NOT {sql}" if negate else sql


class WhereBuilder:
    """Collects `AND`-ed conditions and their parameters."""

    def __init__(self, *conditions: str) -> None:
        self.conditions: list[str] = list(conditions)
        self.params: dict[str, Any] = {}

    def add(self, condition: str, **params: Any) -> WhereBuilder:
        self.conditions.append(condition)
        self.params.update(params)
        return self

    def add_fragment(self, fragment: SqlFragment | None) -> WhereBuilder:
        if fragment is not None:
            self.add(fragment[0], **fragment[1])
        return self

    @property
    def sql(self) -> str:
        return " AND ".join(f"({c})" for c in self.conditions) if self.conditions else "TRUE"
