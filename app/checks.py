import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from .models import Conversation

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
    "doscientos": 200, "trescientos": 300, "cuatrocientos": 400, "quinientos": 500,
    "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900,
}
for k, v in list(UNITS.items()):
    if k.endswith("ientos"):
        UNITS[k[:-2] + "as"] = v

DIGITS = {"cero": "0", "uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4",
          "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9"}

THREATS = ["cobro juridico", "juridic", "embargo", "embarg", "centrales de riesgo",
           "central de riesgo", "reportad", "reporte negativo", "demanda", "abogado",
           "accion legal", "acciones legales", "proceso legal"]

NEGATIONS = {"no", "nunca", "ningun", "ninguna", "ninguno", "sin", "tampoco"}

# "Su pago queda reportado" is the agent doing its job, not a threat.
PAYMENT_REPORT = re.compile(r"\bpago (queda |quedo |esta |fue |ya )?reportad|reportad\w* (el|su) pago\b")

OFFERS = ["descuento", "rebaja", "condon", "refinanc", "cuota", "acuerdo de pago"]

PRODUCTS = ["tarjeta", "prestamo", "credito"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def numbers(text: str) -> List[int]:
    """Amounts said in words or digits: 'un millón doscientos mil' -> 1200000."""
    out = []
    for m in re.finditer(r"\d{1,3}(?:[.,]\d{3})+|\d+", text):
        out.append(int(re.sub(r"[.,]", "", m.group())))

    words = norm(text).split()
    total, cur, seen = 0, 0, False
    for i, w in enumerate(words + ["_"]):
        if w in UNITS:
            cur += UNITS[w]
            seen = True
        elif w == "mil":
            total += max(cur, 1) * 1000
            cur, seen = 0, True
        elif w in ("millon", "millones"):
            total += max(cur, 1) * 1_000_000
            cur, seen = 0, True
        elif w == "y" and seen and i + 1 < len(words) and words[i + 1] in UNITS:
            continue
        else:
            if seen:
                out.append(total + cur)
            total, cur, seen = 0, 0, False
    return out


def doc_digits(text: str) -> str:
    found = re.findall(r"\d", text)
    if len(found) >= 4:
        return "".join(found)
    return "".join(DIGITS[w] for w in norm(text).split() if w in DIGITS)


def dates(text: str, year: int) -> List[date]:
    out = []
    for d, mon in re.findall(r"(\d{1,2}) de (" + "|".join(MONTHS) + ")", norm(text)):
        try:
            out.append(date(year, MONTHS[mon], int(d)))
        except ValueError:
            pass
    return out


WEEKDAYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def weekday_date(text: str, call: date) -> Optional[date]:
    """'el domingo 27' -> the date in the call's month (or the next) where the 27th is a Sunday."""
    m = re.search(r"(" + "|".join(WEEKDAYS) + r") (\d{1,2})\b", norm(text))
    if not m:
        return None
    wd, day = WEEKDAYS.index(m.group(1)), int(m.group(2))
    for k in range(3):
        y, mon = call.year + (call.month + k - 1) // 12, (call.month + k - 1) % 12 + 1
        try:
            d = date(y, mon, day)
        except ValueError:
            continue
        if d.weekday() == wd:
            return d
    return None


def has_any(text: str, words: List[str]) -> Optional[str]:
    t = norm(text)
    for w in words:
        if w in t:
            return w
    return None


def threat_word(text: str) -> Optional[str]:
    """First threat word used as such: skips 'no habrá embargo' but not 'si no paga será reportada'."""
    t = norm(text)
    skipped = []  # spans already ruled out, so "juridic" inside a negated "cobro juridico" stays out too
    for w in THREATS:
        for m in re.finditer(re.escape(w), t):
            if any(a <= m.start() < b for a, b in skipped):
                continue
            before = t[:m.start()].split()[-4:]
            negated = any(x in NEGATIONS and (k == 0 or before[k - 1] != "si")
                          for k, x in enumerate(before) if k >= len(before) - 3)
            if negated or (w == "reportad" and PAYMENT_REPORT.search(t)):
                skipped.append(m.span())
                continue
            return w
    return None


def locate(conv: Conversation, quote: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """Find the turn a quote comes from. Returns the literal text, never the model's version."""
    if not quote:
        return None, None
    q = norm(quote)
    if not q:
        return None, None
    for i, t in enumerate(conv.transcripcion):
        if q in norm(t.texto):
            return i, quote_slice(t.texto, quote)

    # The model may glue a short turn to a system note ("Listo. [Llamada finalizada]").
    inside = [i for i, t in enumerate(conv.transcripcion) if len(norm(t.texto)) >= 4 and norm(t.texto) in q]
    if inside:
        i = max(inside, key=lambda i: len(conv.transcripcion[i].texto))
        return i, conv.transcripcion[i].texto

    best, idx = 0.0, None
    for i, t in enumerate(conv.transcripcion):
        r = SequenceMatcher(None, q, norm(t.texto)).ratio()
        if r > best:
            best, idx = r, i
    if idx is not None and best >= 0.6:
        return idx, conv.transcripcion[idx].texto
    return None, None


def quote_slice(text: str, quote: str) -> str:
    # Keep the model's shorter quote only if it really appears in the turn as written.
    q = quote.strip().strip('"“”')
    return q if q and q in text else text


class Facts:
    """What code can establish on its own about a call, before any LLM."""

    def __init__(self, conv: Conversation):
        self.conv = conv
        c = conv.datos_cliente
        turns = conv.transcripcion
        self.agent = [i for i, t in enumerate(turns) if t.hablante == "agente"]
        self.year = conv.fecha_llamada.year

        self.disclosure = None
        for i in self.agent:
            txt = norm(turns[i].texto)
            if c.monto_vencido_cop in numbers(turns[i].texto) or any(n >= 10000 for n in numbers(turns[i].texto)) \
                    or "saldo" in txt or "vencid" in txt or "deuda" in txt or any(p in txt for p in PRODUCTS):
                self.disclosure = i
                break

        name = norm(c.nombre)
        self.name_turn = None
        self.third_party = False
        for i in self.agent:
            if name in norm(turns[i].texto):
                self.name_turn = i
                reply = self.next_client(i)
                if reply is not None and norm(turns[reply].texto).startswith("no"):
                    self.third_party = True
                break

        self.digits_turn = None
        self.digits_given = None
        for i in self.agent:
            if "digitos" in norm(turns[i].texto):
                reply = self.next_client(i)
                # The client may dodge the first ask, so keep looking until digits come back.
                if reply is not None and doc_digits(turns[reply].texto):
                    self.digits_turn = reply
                    self.digits_given = doc_digits(turns[reply].texto)
                    break

        self.stated_amount = None
        self.amount_turn = None
        self.stated_date = None
        self.date_turn = None
        if self.disclosure is not None:
            for i in self.agent:
                if i < self.disclosure:
                    continue
                big = [n for n in numbers(turns[i].texto) if n >= 10000]
                if self.stated_amount is None and big:
                    self.stated_amount, self.amount_turn = big[0], i
                ds = dates(turns[i].texto, self.year)
                if self.stated_date is None and ds:
                    self.stated_date, self.date_turn = ds[0], i
                if self.stated_amount is not None:
                    break

        self.commit_date = None
        self.commit_turn = None
        for i in self.agent:
            t = norm(turns[i].texto)
            if "compromiso" in t or "registro el pago" in t or "registramos el pago" in t:
                ds = dates(turns[i].texto, self.year)
                if ds:
                    self.commit_date, self.commit_turn = ds[-1], i

        self.threat = None
        self.offer = None
        for i in self.agent:
            if self.threat is None and threat_word(turns[i].texto):
                self.threat = i
            if self.offer is None and has_any(turns[i].texto, OFFERS):
                self.offer = i

    def next_client(self, i: int) -> Optional[int]:
        for j in range(i + 1, len(self.conv.transcripcion)):
            if self.conv.transcripcion[j].hablante == "cliente":
                return j
        return None

    def summary(self) -> dict:
        """Plain facts handed to the LLM so it does not have to re-derive them."""
        c = self.conv.datos_cliente
        return {
            "contesta_un_tercero": self.third_party,
            "turno_donde_se_revela_la_deuda": self.disclosure,
            "digitos_dados_por_el_cliente": self.digits_given,
            "digitos_coinciden": self.digits_given == c.ultimos4_documento if self.digits_given else None,
            "monto_dicho_por_el_agente": self.stated_amount,
            "fecha_vencimiento_dicha_por_el_agente": str(self.stated_date) if self.stated_date else None,
            "fecha_compromiso_confirmada_por_el_agente": str(self.commit_date) if self.commit_date else None,
            "turno_con_palabras_de_amenaza": self.threat,
            "turno_que_menciona_descuentos_o_cuotas": self.offer,
        }


def max_commit_date(conv: Conversation) -> date:
    return conv.fecha_llamada + timedelta(days=5)
