from __future__ import annotations

from app.services.dedupe import QuestionRow, _canonical_text, _clean_text


def make(stem: str, options: list[tuple[str, str]] | None = None, question_type: str = "multiple_choice", **kw):
    return QuestionRow(
        id=kw.get("id", "x"),
        question_type=question_type,
        stem=stem,
        options=[{"id": oid, "text": text} for oid, text in (options or [])],
        subparts=kw.get("subparts", []),
        confidence=kw.get("confidence", 0.9),
        is_discarded=False,
        solution={},
    )


OPTIONS = [("a", "UDP"), ("b", "TCP"), ("c", "IP"), ("d", "ICMP")]


def test_clean_text_normalises_whitespace_and_punctuation() -> None:
    assert _clean_text("  Quale\n protocollo\t ,usa   TCP ?�") == "Quale protocollo,usa TCP?"


def test_canonical_text_drops_case_accents_and_symbols() -> None:
    assert _canonical_text("Perché l'Header è  LUNGO?") == "perche l header e lungo"


def test_fingerprint_ignores_answer_order() -> None:
    shuffled = [("a", "ICMP"), ("b", "IP"), ("c", "TCP"), ("d", "UDP")]
    assert make("Quale protocollo?", OPTIONS).fingerprint == make("Quale protocollo?", shuffled).fingerprint


def test_fingerprint_ignores_numbering_case_and_accents() -> None:
    a = make("3. Quale protocollo è orientato alla connessione?", OPTIONS)
    b = make("12) QUALE protocollo e' orientato alla connessione", OPTIONS)
    assert a.fingerprint == b.fingerprint


def test_fingerprint_separates_different_questions() -> None:
    base = make("Quale protocollo?", OPTIONS)
    assert base.fingerprint != make("Quale protocollo?", [*OPTIONS[:3], ("d", "ARP")]).fingerprint
    assert base.fingerprint != make("Quale servizio?", OPTIONS).fingerprint
    assert base.fingerprint != make("Quale protocollo?", OPTIONS, question_type="open_text").fingerprint


def test_option_text_keeps_symbols_that_change_meaning() -> None:
    # URLs and dotted addresses must not collapse into the same option text.
    a = make("Quale indirizzo?", [("a", "10.0.0.1"), ("b", "10.0.01")])
    b = make("Quale indirizzo?", [("a", "10.0.0.1"), ("b", "10.0.0.1")])
    assert a.fingerprint != b.fingerprint


def test_keeper_score_prefers_confident_and_complete_questions() -> None:
    assert make("Domanda", confidence=0.95).score > make("Domanda", confidence=0.6).score
    assert make("Domanda molto piu lunga e completa").score > make("Domanda").score


def test_exercises_with_different_data_are_not_merged() -> None:
    first = make(
        "ESERCIZIO 1 Un datagramma di 4000 byte attraversa un link con MTU 1500 byte.", question_type="open_text"
    )
    other = make(
        "ESERCIZIO 1 Un datagramma di 3000 byte attraversa un link con MTU 1000 byte.", question_type="open_text"
    )
    renumbered = make(
        "ESERCIZIO 3 Un datagramma di 4000 byte attraversa un link con MTU 1500 byte.", question_type="open_text"
    )
    assert first.fingerprint != other.fingerprint
    assert first.fingerprint == renumbered.fingerprint
