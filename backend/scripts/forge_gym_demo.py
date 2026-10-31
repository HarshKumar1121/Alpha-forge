"""Run the Hypothesis Court and the budgeted planner on the Gym, and print what they decide.

    uv run python scripts/forge_gym_demo.py

Free: everything runs on the synthetic Gym market, nothing reaches BRAIN. It is the end-to-end
proof that the research layer works through the simulator Protocol — the same code will spend
real simulations once the engine adapter lands (Phase 6).

The Gym has an answer key, so you can check the verdicts: analyst revisions are a real slow
effect, book-to-price only works in small caps, news sentiment died in 2022, and alt_5 is noise.
"""

import asyncio
import sys
import time

from alpha_harness.forge.alpha_types import load
from alpha_harness.forge.local.client import GymSimulator
from alpha_harness.forge.local.engine import LOCAL_OPERATORS
from alpha_harness.forge.research.court import Court
from alpha_harness.forge.research.planner import Researcher
from alpha_harness.forge.store import InMemoryResearchStore

HYPOTHESES = [
    ("Analysts revise slowly, and prices drift after revisions", ["anl_revision"], "weeks"),
    ("Cheap stocks beat expensive ones", ["book_to_price"], "months"),
    ("Good news today means higher returns over the next weeks", ["news_sentiment"], "weeks"),
]


async def main() -> None:
    gym = GymSimulator()
    store = InMemoryResearchStore()
    market = gym.market.fields
    regular = load("regular")
    started = time.monotonic()
    print(f"Gym: {gym.market.n_stocks} stocks x {len(market['returns'])} days. Free; no BRAIN.\n")

    for i, (title, fields, horizon) in enumerate(HYPOTHESES, 1):
        hypothesis = {"id": i, "title": title, "expected_sign": "+", "horizon": horizon}
        court = Court(
            store,
            gym,
            regular,
            hypothesis,
            fields,
            local_data={f: market[f] for f in fields} | {"cap": market["cap"]},
            available=set(market),
            operators=set(LOCAL_OPERATORS),
            scope="gym",
        )
        case = await court.run()
        print("\n".join(case.lines()))
        print("-" * 96)

    fields = ["anl_revision", "alt_5"]
    print(f"\nPLANNER: 30 simulations over {fields} (one real effect, one noise field)\n")
    report = await Researcher(
        store,
        gym,
        regular,
        {"id": 99, "title": "planner demo"},
        fields,
        30,
        local_data={f: market[f] for f in fields} | {"cap": market["cap"]},
        available=set(market),
        operators=set(LOCAL_OPERATORS),
        scope="gym",
        log=print,
    ).run()
    print("\n".join(report.lines()))
    print(f"\nDone in {time.monotonic() - started:.0f}s.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
