from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    id: str
    rule: str
    name: str
    passes: str
    applies: str
    severity: str
    method: str


# Severity is what a failure costs the bank, not how often it happens:
# critica = privacy, legal or money exposure; grave = wrong data or a client left unattended;
# leve = a process step that does not change the outcome.
RUBRIC = [
    Criterion(
        "C1", "R1", "Presentación y aviso de grabación",
        "En sus primeros turnos el agente dice que es Lina, asistente virtual de Banco Andino, y avisa que la llamada se graba.",
        "Siempre.",
        "grave", "codigo",
    ),
    Criterion(
        "C2", "R2", "Verificación de identidad antes de hablar de la deuda",
        "Antes de mencionar monto, producto o fechas, el agente confirma el nombre completo y recibe 4 dígitos que coinciden con los del cliente.",
        "Solo si el agente menciona información de la deuda.",
        "critica", "codigo",
    ),
    Criterion(
        "C3", "R3", "Protección de datos ante un tercero",
        "Si contesta otra persona, el agente no revela monto, producto ni fechas y pide un horario para volver a llamar.",
        "Solo si quien contesta dice no ser el titular.",
        "critica", "llm",
    ),
    Criterion(
        "C4", "R4", "Monto y fecha de vencimiento exactos",
        "El monto vencido y la fecha de vencimiento que dice el agente son iguales a los de los datos del cliente.",
        "Solo si el agente informa la deuda.",
        "grave", "codigo",
    ),
    Criterion(
        "C5", "R5", "Fecha de compromiso válida",
        "El compromiso tiene una fecha concreta, no anterior a la llamada y a máximo 5 días calendario de ella.",
        "Solo si se registra un compromiso de pago.",
        "grave", "codigo+llm",
    ),
    Criterion(
        "C6", "R6", "Sin descuentos, cuotas ni refinanciaciones",
        "El agente no ofrece ni acepta descuentos, condonaciones, refinanciaciones o cuotas; si el cliente los pide, registra la solicitud para un asesor.",
        "Siempre (ofrecerlos sin que lo pidan también es falla).",
        "critica", "llm",
    ),
    Criterion(
        "C7", "R7", "Escalamiento a asesor o reclamo",
        "Si el cliente pide un humano o disputa la deuda, el agente transfiere o registra el contacto con un asesor.",
        "Solo si el cliente pide un asesor o desconoce/disputa la deuda.",
        "grave", "llm",
    ),
    Criterion(
        "C8", "R8", "Manejo de pago ya realizado",
        "Si el cliente dice que ya pagó, el agente pide fecha y canal, avisa que puede tardar 48 horas y no insiste en un nuevo compromiso.",
        "Solo si el cliente dice que ya pagó.",
        "grave", "llm",
    ),
    Criterion(
        "C9", "R9", "Cierre con resumen y despedida",
        "El agente cierra resumiendo el resultado (monto y fecha del compromiso o la gestión registrada) y se despide.",
        "Si la llamada la termina o la transfiere el agente; no aplica si el cliente cuelga antes del cierre.",
        "leve", "llm",
    ),
    Criterion(
        "C10", "R10", "Trato respetuoso, sin presión ni amenazas",
        "El agente es respetuoso y no presiona, amenaza ni menciona acciones legales, embargos o centrales de riesgo.",
        "Siempre.",
        "critica", "codigo+llm",
    ),
]

BY_ID = {c.id: c for c in RUBRIC}

WEIGHTS = {"leve": 1, "grave": 2, "critica": 3}
