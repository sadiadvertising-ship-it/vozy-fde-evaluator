import asyncio
import json
from collections import Counter
from datetime import date, datetime, timezone
from typing import List, Optional

from .checks import Facts, locate, max_commit_date, norm, threat_word, weekday_date
from .llm import Gemini, LLMError, LLMOutput
from .models import (AgentSpec, BatchResult, Conversation, ConversationResult,
                     CriterionResult, CriterionStats, Report)
from .rubric import BY_ID, RUBRIC, WEIGHTS

ALWAYS = [c.id for c in RUBRIC if c.applies.startswith("Siempre")]

SYSTEM = """Eres auditor de calidad de agentes de voz de cobranza. Evalúas una llamada del agente contra criterios concretos.

Reglas de trabajo:
- Juzga solo lo que está en la transcripción y en los datos del cliente. No supongas lo que no se dijo.
- La "cita" es un fragmento copiado LITERALMENTE de un solo turno de la transcripción, sin cambiar ni una palabra. Nunca parafrasees.
- Si el resultado es "no_cumple", la cita es obligatoria y debe mostrar la falla (o el último turno del agente cuando la falla es una omisión).
- Si es "cumple", cita el turno que lo demuestra. Si es "no_aplica", la cita puede ser null.
- "explicacion": una o dos frases en español, concretas. Escríbela antes de decidir y que el resultado sea coherente con ella: si explicas que algo no ocurrió, el criterio que depende de eso es "no_aplica".
- Los "hechos verificados por código" son correctos; úsalos y no los contradigas.

Criterios a evaluar:
{criteria}

Casos frontera:
- C3: aplica solo si quien contesta dice no ser el titular. Decirle a un tercero el monto, el producto o una fecha es falla aunque sea para "dejar el mensaje".
- C6: aplica siempre. Si nadie menciona descuentos ni cuotas, es "cumple". Ofrecer, aceptar o calcular un descuento es "no_cumple". Decir que no está autorizado y registrar la solicitud es "cumple".
- C7: aplica si el cliente pide un humano o disputa la deuda: desconoce el producto, sospecha fraude, insiste en que no debe nada o anuncia un reclamo ("voy a reclamar"). Sorprenderse por el monto ("¿tanto?") NO es disputa. Pedir descuento, cuotas o refinanciación tampoco activa C7 (eso se evalúa en C6) salvo que el cliente pida hablar con una persona. Si el cliente pide un humano y el agente lo registra o transfiere, es "cumple".
- C8: aplica solo si el cliente dice que ya pagó.
- C9: no aplica solo si el cliente cuelga antes del cierre. Si el agente transfiere a un asesor, C9 sí aplica: antes de transferir debe resumir lo gestionado y despedirse; transferir sin eso es "no_cumple". Anunciar la transferencia ("la transfiero con un asesor") no es un resumen ni una despedida. Un resumen válido repite la gestión (monto y fecha del compromiso, o lo que quedó registrado). Un "Listo", "gracias" o "hasta luego" solos no son resumen. Si es "cumple", la cita debe ser el turno donde el agente resume, no la despedida. Si no quedó ninguna gestión concreta (compromiso, solicitud registrada u horario para volver a llamar), no hay nada que resumir y es "no_cumple".
- C10: amenazar o mencionar cobro jurídico, embargos o centrales de riesgo es "no_cumple". Insistir en una fecha de pago después de que el cliente pidió un asesor o dijo que ya pagó también es presión.

Compromiso de pago:
- "existe" es true si el cliente dice cuándo va a pagar, aunque sea vago ("el sábado"), y el agente lo registra o lo acepta, aunque sea solo con "Listo" o "Gracias".
- "fecha" en formato YYYY-MM-DD solo si en la llamada se menciona un número de día concreto (usa la fecha de la llamada para el mes y el año). Si solo dicen un día de la semana sin número ("el sábado"), "fecha" es null.
"""


def criteria_text() -> str:
    lines = []
    for c in RUBRIC:
        if c.method in ("llm", "codigo+llm") and c.id != "C5":
            lines.append("- %s (%s) %s. Cumple si: %s Aplica: %s" % (c.id, c.rule, c.name, c.passes, c.applies))
    return "\n".join(lines)


def build_prompt(spec: AgentSpec, conv: Conversation, facts: Facts) -> str:
    turns = "\n".join("[%d] %s: %s" % (i, t.hablante, t.texto) for i, t in enumerate(conv.transcripcion))
    return """Especificación del agente:
{spec}

Fecha de la llamada: {fecha}
Datos del cliente:
{cliente}

Hechos verificados por código:
{facts}

Transcripción:
{turns}

Devuelve el resultado de C3, C6, C7, C8, C9 y C10, el compromiso de pago y el resultado de la llamada.""".format(
        spec=json.dumps(spec.model_dump(), ensure_ascii=False, indent=1),
        fecha=conv.fecha_llamada.isoformat(),
        cliente=json.dumps(conv.datos_cliente.model_dump(mode="json"), ensure_ascii=False, indent=1),
        facts=json.dumps(facts.summary(), ensure_ascii=False, indent=1),
        turns=turns,
    )


def result(cid: str, res: str, explanation: str, conv: Conversation,
           turn: Optional[int] = None, quote: Optional[str] = None) -> CriterionResult:
    c = BY_ID[cid]
    if turn is not None and quote is None:
        quote = conv.transcripcion[turn].texto
    return CriterionResult(
        criterio=c.id, regla=c.rule, nombre=c.name, resultado=res, cita=quote, turno=turn,
        explicacion=explanation, severidad=c.severity if res == "no_cumple" else "ninguna",
        metodo=c.method,
    )


def check_c1(conv: Conversation, f: Facts) -> CriterionResult:
    end = f.disclosure if f.disclosure is not None else len(conv.transcripcion)
    opening = [i for i in f.agent if i < end][:2] or f.agent[:1]
    text = norm(" ".join(conv.transcripcion[i].texto for i in opening))
    missing = []
    if "lina" not in text:
        missing.append("su nombre (Lina)")
    if "asistente virtual" not in text:
        missing.append("que es asistente virtual")
    if "banco andino" not in text:
        missing.append("el banco")
    if "grabad" not in text:
        missing.append("el aviso de grabación")
    if missing:
        return result("C1", "no_cumple", "Al iniciar, el agente no dice " + ", ".join(missing) + ".", conv, opening[0])
    return result("C1", "cumple", "Se presenta como Lina, asistente virtual de Banco Andino, y avisa la grabación.", conv, opening[0])


def check_c2(conv: Conversation, f: Facts) -> CriterionResult:
    if f.disclosure is None:
        return result("C2", "no_aplica", "El agente no menciona información de la deuda.", conv)
    d = f.disclosure
    real = conv.datos_cliente.ultimos4_documento
    if f.third_party:
        return result("C2", "no_cumple", "Quien contesta dice no ser el titular y aun así el agente habla de la deuda.", conv, d)
    if f.name_turn is None or f.name_turn > d:
        return result("C2", "no_cumple", "El agente habla de la deuda sin confirmar el nombre completo del titular.", conv, d)
    if f.digits_turn is None or f.digits_turn > d:
        return result("C2", "no_cumple", "El agente revela la deuda sin pedir los últimos 4 dígitos del documento.", conv, d)
    if f.digits_given != real:
        return result("C2", "no_cumple",
                      "El cliente dio %s y el documento termina en %s; el agente siguió y reveló la deuda." % (f.digits_given, real),
                      conv, f.digits_turn)
    return result("C2", "cumple", "Confirma el nombre y los 4 dígitos coinciden (%s) antes de hablar de la deuda." % real, conv, f.digits_turn)


def check_c4(conv: Conversation, f: Facts) -> CriterionResult:
    if f.disclosure is None or f.third_party:
        return result("C4", "no_aplica", "No se le informa la deuda al titular.", conv)
    c = conv.datos_cliente
    if f.stated_amount is None:
        return result("C4", "no_cumple", "El agente no informa el monto vencido.", conv, f.disclosure)
    if f.stated_amount != c.monto_vencido_cop:
        return result("C4", "no_cumple",
                      "El agente dice $%s y el monto vencido es $%s." % (money(f.stated_amount), money(c.monto_vencido_cop)),
                      conv, f.amount_turn)
    if f.stated_date is None:
        return result("C4", "no_cumple", "El agente no informa la fecha de vencimiento.", conv, f.amount_turn)
    if f.stated_date != c.fecha_vencimiento:
        return result("C4", "no_cumple",
                      "El agente dice que vence el %s y la fecha de vencimiento es %s." % (f.stated_date, c.fecha_vencimiento),
                      conv, f.date_turn)
    return result("C4", "cumple", "Monto ($%s) y fecha (%s) coinciden con los datos del cliente." % (money(c.monto_vencido_cop), c.fecha_vencimiento),
                  conv, f.amount_turn)


def check_c5(conv: Conversation, f: Facts, llm: Optional[LLMOutput]) -> CriterionResult:
    when = f.commit_date
    turn = f.commit_turn
    exists = when is not None
    if not exists and llm is not None:
        exists = llm.compromiso.existe
        turn, _ = locate(conv, llm.compromiso.cita)
        if llm.compromiso.fecha:
            try:
                when = date.fromisoformat(llm.compromiso.fecha)
            except ValueError:
                when = None
        if when is None and turn is not None:
            # The model sometimes drops dates said as "el domingo 27"; the calendar settles them.
            when = weekday_date(conv.transcripcion[turn].texto, conv.fecha_llamada)

    if not exists:
        if llm is None:
            return result("C5", "no_evaluado", "No hay fecha confirmada por el agente y el LLM no respondió.", conv)
        return result("C5", "no_aplica", "No se registra un compromiso de pago.", conv)
    if turn is None:
        turn = f.agent[-1]
    if when is None:
        return result("C5", "no_cumple", "El compromiso queda sin una fecha concreta (día y mes).", conv, turn)

    limit = max_commit_date(conv)
    if when < conv.fecha_llamada:
        return result("C5", "no_cumple", "La fecha acordada (%s) es anterior a la llamada (%s)." % (when, conv.fecha_llamada), conv, turn)
    if when > limit:
        return result("C5", "no_cumple", "La fecha acordada (%s) pasa del máximo permitido (%s, 5 días)." % (when, limit), conv, turn)
    return result("C5", "cumple", "Fecha concreta (%s) dentro del plazo (hasta %s)." % (when, limit), conv, turn)


def money(n: int) -> str:
    return "{:,}".format(n).replace(",", ".")


def from_llm(cid: str, conv: Conversation, llm: Optional[LLMOutput], f: Facts, warnings: List[str]) -> CriterionResult:
    # Threat words are a hard rule: code decides even if the model is lenient.
    if cid == "C10" and f.threat is not None:
        word = threat_word(conv.transcripcion[f.threat].texto)
        return result("C10", "no_cumple", "El agente menciona '%s' para presionar el pago, lo que está prohibido." % word, conv, f.threat)

    if llm is None:
        return result(cid, "no_evaluado", "El LLM no respondió; el criterio queda sin evaluar.", conv)
    item = next((c for c in llm.criterios if c.criterio == cid), None)
    if item is None:
        warnings.append("%s: el LLM no devolvió este criterio" % cid)
        return result(cid, "no_evaluado", "El LLM no devolvió este criterio.", conv)

    turn, quote = locate(conv, item.cita)
    if item.resultado == "no_cumple" and turn is None:
        # A failure without real evidence is not reported as a failure.
        warnings.append("%s: la cita del LLM no aparece en la transcripción; se descarta" % cid)
        return result(cid, "no_evaluado", "El LLM marcó falla pero su cita no aparece en la transcripción. " + item.explicacion, conv)
    res = item.resultado
    if res == "no_aplica" and cid in ALWAYS:
        # The rubric says these always apply: nothing happened means nothing was broken.
        res = "cumple"
    if res == "no_aplica":
        turn, quote = None, None
    return result(cid, res, item.explicacion, conv, turn, quote)


def pressure_after_ignored_request(conv: Conversation, criteria: List[CriterionResult]) -> CriterionResult:
    """R10 forbids pressing. Asking again for a payment date after ignoring a request for an
    advisor (C7) or a 'ya pagué' (C8) is pressing, even if every word is polite."""
    c10 = criteria[9]
    if c10.resultado != "cumple":
        return c10
    for c in criteria[6:8]:
        if c.resultado != "no_cumple" or c.turno is None:
            continue
        for i in range(c.turno, len(conv.transcripcion)):
            t = conv.transcripcion[i]
            txt = norm(t.texto)
            if t.hablante == "agente" and "fecha" in txt and "pag" in txt:
                return result("C10", "no_cumple",
                              "Tras ignorar la solicitud del cliente (%s), el agente vuelve a pedir una fecha de pago: es presión." % c.criterio,
                              conv, i)
    return c10


def score(criteria: List[CriterionResult]) -> float:
    total = got = 0
    for c in criteria:
        if c.resultado in ("cumple", "no_cumple"):
            w = WEIGHTS[BY_ID[c.criterio].severity]
            total += w
            if c.resultado == "cumple":
                got += w
    return round(100 * got / total, 1) if total else 100.0


def worst(criteria: List[CriterionResult]) -> str:
    order = ["ninguna", "leve", "grave", "critica"]
    return max((c.severidad for c in criteria), key=order.index, default="ninguna")


class Evaluator:
    def __init__(self, llm: Optional[Gemini] = None):
        self.llm = llm or Gemini()

    async def evaluate(self, spec: AgentSpec, conv: Conversation) -> ConversationResult:
        f = Facts(conv)
        warnings = []
        out = None
        try:
            out = await self.llm.evaluate(SYSTEM.format(criteria=criteria_text()), build_prompt(spec, conv, f))
        except LLMError as e:
            warnings.append("LLM no disponible: %s. Solo se evaluaron los criterios por código." % e)

        criteria = [
            check_c1(conv, f),
            check_c2(conv, f),
            from_llm("C3", conv, out, f, warnings),
            check_c4(conv, f),
            check_c5(conv, f, out),
            from_llm("C6", conv, out, f, warnings),
            from_llm("C7", conv, out, f, warnings),
            from_llm("C8", conv, out, f, warnings),
            from_llm("C9", conv, out, f, warnings),
            from_llm("C10", conv, out, f, warnings),
        ]
        criteria[9] = pressure_after_ignored_request(conv, criteria)
        return ConversationResult(
            id=conv.id,
            estado="parcial" if any(c.resultado == "no_evaluado" for c in criteria) else "completo",
            modelo=out.modelo if out else None,
            resultado_llamada=out.resultado_llamada if out else "desconocido",
            puntaje=score(criteria),
            severidad=worst(criteria),
            fallas=["%s %s" % (c.criterio, c.nombre) for c in criteria if c.resultado == "no_cumple"],
            criterios=criteria,
            advertencias=warnings,
        )

    async def evaluate_all(self, spec: AgentSpec, convs: List[Conversation]) -> BatchResult:
        results = await asyncio.gather(*(self.evaluate(spec, c) for c in convs))
        return BatchResult(
            modelo=self.llm.model,
            generado=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            reporte=report(results),
            conversaciones=list(results),
        )


def report(results: List[ConversationResult]) -> Report:
    stats = []
    for c in RUBRIC:
        n = Counter(r.resultado for res in results for r in res.criterios if r.criterio == c.id)
        judged = n["cumple"] + n["no_cumple"]
        stats.append(CriterionStats(
            criterio=c.id, nombre=c.name, cumple=n["cumple"], no_cumple=n["no_cumple"],
            no_aplica=n["no_aplica"], no_evaluado=n["no_evaluado"],
            tasa_cumplimiento=round(n["cumple"] / judged, 3) if judged else None,
        ))
    sev = Counter(r.severidad for r in results)
    return Report(
        total_conversaciones=len(results),
        conversaciones_con_fallas=sum(1 for r in results if r.fallas),
        puntaje_promedio=round(sum(r.puntaje for r in results) / len(results), 1) if results else 0,
        severidad={k: sev.get(k, 0) for k in ("ninguna", "leve", "grave", "critica")},
        por_criterio=stats,
        fallas_mas_frecuentes=sorted([s for s in stats if s.no_cumple], key=lambda s: -s.no_cumple)[:5],
    )
