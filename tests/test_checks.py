import json
from datetime import date
from pathlib import Path

import pytest

from app.checks import Facts, dates, doc_digits, locate, numbers, weekday_date
from app.evaluator import check_c1, check_c2, check_c4, check_c5
from app.models import Dataset

ROOT = Path(__file__).resolve().parent.parent
DATA = Dataset(**json.loads((ROOT / "data" / "conversaciones_prueba_fde.json").read_text()))
GOLDEN = json.loads((ROOT / "tests" / "golden.json").read_text())


def test_numbers_in_words():
    assert numbers("un millón doscientos cincuenta mil pesos") == [1250000]
    assert numbers("dos millones seiscientos veinte mil") == [2620000]
    assert numbers("trescientos diez mil pesos") == [310000]
    assert numbers("ochocientos setenta mil") == [870000]
    assert numbers("$1.250.000") == [1250000]
    assert numbers("treinta y cinco") == [35]


def test_numbers_ignore_plain_text():
    assert numbers("Buenos días, le habla Lina") == []


def test_doc_digits():
    assert doc_digits("4821.") == "4821"
    assert doc_digits("A ver... uno, tres, cinco, dos.") == "1352"
    assert doc_digits("Primero dígame quién habla.") == ""


def test_dates():
    assert dates("con vencimiento el 15 de septiembre", 2026) == [date(2026, 9, 15)]
    assert dates("vence el 31 de febrero", 2026) == []
    assert dates("el sábado", 2026) == []


def test_locate_returns_literal_text():
    conv = DATA.conversaciones[0]
    turn, quote = locate(conv, "esta llamada está siendo grabada")
    assert turn == 0 and quote == "esta llamada está siendo grabada"
    # Accents or punctuation changed by the model still map to the real turn.
    turn, quote = locate(conv, "esta llamada esta siendo grabada")
    assert turn == 0 and quote in conv.transcripcion[0].texto
    assert locate(conv, "le ofrezco un descuento del 50%") == (None, None)


@pytest.mark.parametrize("conv", DATA.conversaciones, ids=lambda c: c.id)
def test_code_criteria_match_golden(conv):
    f = Facts(conv)
    gold = GOLDEN[conv.id]
    assert check_c1(conv, f).resultado == gold["C1"]
    assert check_c2(conv, f).resultado == gold["C2"]
    assert check_c4(conv, f).resultado == gold["C4"]
    # Without the LLM, C5 can only judge calls where the agent confirms a dated commitment.
    c5 = check_c5(conv, f, None)
    assert c5.resultado in (gold["C5"], "no_evaluado")


@pytest.mark.parametrize("conv", DATA.conversaciones, ids=lambda c: c.id)
def test_failures_always_quote_the_transcript(conv):
    f = Facts(conv)
    texts = [t.texto for t in conv.transcripcion]
    for r in (check_c1(conv, f), check_c2(conv, f), check_c4(conv, f), check_c5(conv, f, None)):
        if r.resultado == "no_cumple":
            assert r.cita and r.cita in texts[r.turno]


def test_weekday_date_matches_calendar():
    call = date(2026, 9, 22)
    assert weekday_date("El domingo 27 lo pago.", call) == date(2026, 9, 27)
    assert weekday_date("el sábado 3", call) == date(2026, 10, 3)
    assert weekday_date("el lunes 27", call) is None
    assert weekday_date("el sábado", call) is None
