from __future__ import annotations

from app.services import filters


def test_parse_tag_values_splits_dedupes_and_unquotes() -> None:
    assert filters.parse_tag_values(" reti, \"tcp-ip\" ,\nrouting, reti, , '' ") == ["reti", "tcp-ip", "routing"]
    assert filters.parse_tag_values(None) == []
    assert len(filters.parse_tag_values(",".join(str(i) for i in range(100)))) == 30


def test_any_tag_uses_one_parameter_per_value() -> None:
    sql, params = filters.any_tag(["dns", "nat"], param_prefix="t")
    assert params == {"t_0": "dns", "t_1": "nat"}
    assert sql.count(":t_0") == 3 and ":t_1" in sql
    assert filters.any_tag([]) is None


def test_has_correction_can_be_negated() -> None:
    assert filters.has_correction(user_param="u").startswith("EXISTS")
    assert filters.has_correction(user_param="u", negate=True).startswith("NOT EXISTS")


def test_where_builder_combines_conditions_and_params() -> None:
    where = filters.WhereBuilder("q.is_discarded = false")
    where.add("q.question_type = :qt", qt="open_text").add_fragment(filters.any_tag(["dns"])).add_fragment(None)
    assert len(where.conditions) == 3
    assert where.sql.startswith("(q.is_discarded = false) AND (q.question_type = :qt) AND (EXISTS")
    assert where.params == {"qt": "open_text", "tag_0": "dns"}
    assert filters.WhereBuilder().sql == "TRUE"
