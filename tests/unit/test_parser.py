from __future__ import annotations

from uuid import uuid4

from app.services.parser import parse_unisa_questions


def parse(text: str):
    return parse_unisa_questions(uuid4(), [text])


def test_domanda_blocks_become_multiple_choice() -> None:
    questions = parse(
        """DOMANDA 1
Quale livello si occupa dell'instradamento?
a. Trasporto
b. Rete
c. Collegamento
d. Applicazione
DOMANDA 2
Che cosa misura il throughput?
a. La latenza
b. La quantita di dati trasferiti per unita di tempo
"""
    )
    assert [(q.section, q.number_in_section, q.question_type) for q in questions] == [
        ("quiz", 1, "multiple_choice"),
        ("quiz", 2, "multiple_choice"),
    ]
    assert questions[0].stem == "Quale livello si occupa dell'instradamento?"
    assert [(o.id, o.text) for o in questions[0].options][1] == ("b", "Rete")
    assert questions[0].quality.needs_review is False


def test_numbered_questions_and_inline_options_are_split() -> None:
    # pypdf often glues options onto one line; the parser re-inserts the breaks.
    questions = parse(
        "1. Quanti byte ha un header UDP? a. 8 b. 20 c. 40 d. 60\n2. Cosa fa ARP? a. Risolve MAC b. Risolve nomi"
    )
    assert [len(q.options) for q in questions] == [4, 2]
    assert questions[0].options[0].text == "8"
    assert questions[1].stem == "Cosa fa ARP?"


def test_question_without_options_is_kept_as_open_question_for_review() -> None:
    questions = parse("1. Descrivere il protocollo DHCP.\n2. Cosa fa DNS? a. Risolve nomi b. Instrada")
    open_question = next(q for q in questions if q.question_type == "open_text")
    assert open_question.section == "teoria"
    assert open_question.quality.needs_review is True


def test_theory_and_exercise_sections() -> None:
    questions = parse(
        """DOMANDA TEORIA
Spiegare il three-way handshake di TCP.
ESERCIZIO 1
Si consideri una rete 192.168.0.0/24.
1) Suddividere la rete in quattro sottoreti.
2) Indicare l'indirizzo di broadcast di ciascuna.
ESERCIZIO 2
Calcolare il ritardo end-to-end.
"""
    )
    theory, ex1, ex2 = sorted(questions, key=lambda q: (q.section != "teoria", q.number_in_section))
    assert (theory.section, theory.question_type) == ("teoria", "open_text")
    assert "three-way handshake" in theory.stem
    assert (ex1.question_type, [s.id for s in ex1.subparts]) == ("multi_part_open", ["1", "2"])
    assert ex2.question_type == "open_text"


def test_header_lines_are_ignored() -> None:
    questions = parse(
        "Cognome ____ Nome ____ Matricola ____\nRisposte corrette\n"
        "DOMANDA 1\nCosa e' un socket?\na. Un'interfaccia\nb. Un cavo"
    )
    assert len(questions) == 1
    assert "Matricola" not in questions[0].stem


def test_repeated_question_numbers_do_not_collide() -> None:
    questions = parse("DOMANDA 1\nPrima?\na. si\nb. no\nDOMANDA 1\nSeconda?\na. si\nb. no")
    assert sorted(q.number_in_section for q in questions) == [1, 2]


def test_rule_tags_are_attached() -> None:
    (question,) = parse("DOMANDA 1\nQuale porta usa DNS?\na. 53\nb. 80")
    assert {"dns", "reti"} <= set(question.tags)


def test_ocr_option_without_space_is_recognised() -> None:
    (question,) = parse("DOMANDA 1\nQuale record DNS?\na. NS\nb.A\nc. MX\nd.CNAME")
    assert [(o.id, o.text) for o in question.options] == [("a", "NS"), ("b", "A"), ("c", "MX"), ("d", "CNAME")]


def test_abbreviations_are_not_mistaken_for_options() -> None:
    (question,) = parse("DOMANDA 1\nIl c.d. collo di bottiglia\nc.d. limita il throughput?\na. Si\nb. No")
    assert [o.id for o in question.options] == ["a", "b"]
    assert "c.d. limita" in question.stem
