"""Run the evaluator on the sample file and compare it with the hand-made labels in tests/golden.json."""
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.evaluator import Evaluator  # noqa: E402
from app.models import Dataset  # noqa: E402


async def main(only):
    data = Dataset(**json.loads((ROOT / "data" / "conversaciones_prueba_fde.json").read_text()))
    gold = json.loads((ROOT / "tests" / "golden.json").read_text())
    convs = [c for c in data.conversaciones if not only or c.id in only]

    ev = Evaluator()
    t = time.time()
    out = await ev.evaluate_all(data.especificacion_agente, convs)
    secs = time.time() - t

    ok, total, misses = 0, 0, Counter()
    for conv in out.conversaciones:
        for c in conv.criterios:
            want = gold[conv.id][c.criterio]
            total += 1
            if c.resultado == want:
                ok += 1
            else:
                misses[c.criterio] += 1
                print("%s %s: got %s, want %s | %s | %s" % (conv.id, c.criterio, c.resultado, want, c.cita, c.explicacion))
        for w in conv.advertencias:
            print("%s warning: %s" % (conv.id, w))

    print("\nagreement %d/%d (%.1f%%) in %.0fs" % (ok, total, 100 * ok / total, secs))
    print("misses by criterion:", dict(misses))
    print("tokens in %d, out %d (%d calls)" % (ev.llm.tokens_in, ev.llm.tokens_out, len(convs)))
    return out


if __name__ == "__main__":
    asyncio.run(main(set(sys.argv[1:])))
