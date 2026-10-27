"""Generate -> validate/repair -> critique/refine -> select, for one prompt image.

Each chain is: one generation, then REFINE_ROUNDS self-critique refinements that each see the
current module's renders and rate them (PENALTY), then a choice between the best-rated earlier
version and the last one. With CHAINS > 1, chains run their model calls concurrently and a final
selection picks across chain winners.

Replay safety: model calls may run concurrently, but every render happens in a fixed order
(stage by stage, chain by chain), request ids are fixed per role, and no decision depends on time,
randomness or completion order.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Unpack, cast

from openai import APIConnectionError, APIError
from openai.types.responses import Response, ResponseInputParam
from openai.types.responses.response_create_params import ResponseCreateParamsBase
from renderer_client import Renderer, RenderOptions, RenderRejected
from witness_client import WitnessError

from . import prompts

MODEL = "gpt-6.1-sol"
CHAINS = 3
REFINE_ROUNDS = 2
MAX_REPAIRS = 2
GEN_EFFORT = "high"
REFINE_EFFORT = "high"
REPAIR_EFFORT = "medium"
SELECT_EFFORT = "medium"
MAX_OUTPUT_TOKENS = 64000
SELECT_MAX_OUTPUT_TOKENS = 4096

# Credits per token (= USD per 1M tokens), from the competition rate file.
PRICES = {"gpt-6.1-sol": (2.0, 10.0), "gpt-6-sol": (2.0, 10.0), "gpt-6-astra": (10.0, 50.0)}
CALL_LIMIT = 60
USD_LIMIT = 5.0
USD_RESERVE = 0.8  # do not start a stage unless this much head-room remains per started call

# Judge-camera previews, as two 2x2 grids (production judges front_left/right, front_below/above).
FRONT = RenderOptions(thetas=(30, 330, 0, 0), phis=(0, 0, 15, -30), img_size=384, bg_color="ffffff",
                      lighting="neutral")
SIDES = RenderOptions(thetas=(90, 180, 270, 0), phis=(0, 0, 0, -90), img_size=384, bg_color="ffffff",
                      lighting="neutral")

NORMALIZATION = Path(__file__).with_name("normalize.js").read_text(encoding="utf-8")
CODE_RE = re.compile(r"```(?:javascript|js)?\s*\n(.*?)```", re.DOTALL)
PENALTY_RE = re.compile(r"PENALTY:\s*([0-9]+(?:\.[0-9]+)?)")
BEST_RE = re.compile(r"BEST:\s*([A-Z])")


class ModelCalls(Protocol):
    def create(self, *, request_id: int = 0, **params: Unpack[ResponseCreateParamsBase]) -> Response: ...


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def extract_code(text: str | None) -> str | None:
    if not text:
        return None
    blocks = [b for b in CODE_RE.findall(text) if "export default" in b]
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    if "export default function" in text:
        return text[text.index("export default function") :].strip() + "\n"
    return None


def normalize(source: str) -> str:
    """Re-centre and rescale into the unit cube (operator reference wrapper)."""
    exported = re.match(r"export\s+default\s+", source)
    if exported is None:
        return source
    return NORMALIZATION.replace("__GENERATED_SOURCE__", source[exported.end() :])


@dataclass
class Candidate:
    source: str
    front: bytes
    sides: bytes
    penalty: float | None = None  # self-rating, filled in by the next refine call


@dataclass
class Chain:
    index: int
    history: list[Candidate] = field(default_factory=list)


@dataclass
class Ledger:
    calls: int = 0
    usd: float = 0.0

    def affordable(self, n_calls: int) -> bool:
        return self.calls + n_calls <= CALL_LIMIT and self.usd + n_calls * USD_RESERVE <= USD_LIMIT

    def charge(self, model: str, response: Response | None) -> None:
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            pin, pout = PRICES[model]
            self.usd += (usage.input_tokens * pin + usage.output_tokens * pout) / 1e6


class Miner:
    def __init__(self, image: bytes, calls: ModelCalls, renderer: Renderer):
        self.reference = data_url(image)
        self.calls = calls
        self.renderer = renderer
        self.ledger = Ledger()

    # ---- model calls -------------------------------------------------------------------------

    def ask(self, request_id: int, text: str, images: list[str], effort: str, max_tokens: int) -> str | None:
        content = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url": u, "detail": "high"} for u in images]
        response = None
        try:
            response = self.calls.create(
                request_id=request_id,
                model=MODEL,
                store=False,
                service_tier="default",
                reasoning={"effort": effort},
                max_output_tokens=max_tokens,
                instructions=prompts.SYSTEM,
                input=cast(ResponseInputParam, [{"role": "user", "content": content}]),
            )
        except APIConnectionError:
            raise  # infrastructure: let the runner recover the same exchange
        except (WitnessError, APIError) as error:
            if isinstance(error, WitnessError) and error.source != "provider":
                raise  # infrastructure: let the runner recover the same exchange
            return None
        finally:
            self.ledger.charge(MODEL, response)
        if response.status != "completed":
            return None
        return response.output_text

    def parallel(self, jobs: list[Callable[[], str | None]]) -> list[str | None]:
        if len(jobs) == 1:
            return [jobs[0]()]
        with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="sn17-call") as pool:
            futures = [pool.submit(job) for job in jobs]
            return [f.result() for f in futures]

    # ---- rendering (always sequential, fixed order) -----------------------------------------

    def realize(self, request_base: int, source: str | None) -> Candidate | None:
        """Render a module; on rejection ask for a repair, up to MAX_REPAIRS times."""
        for attempt in range(MAX_REPAIRS + 1):
            if source is None:
                return None
            source = normalize(source)
            try:
                front = self.renderer.render(source, FRONT)
                sides = self.renderer.render(source, SIDES)
                return Candidate(source, front, sides)
            except RenderRejected as rejection:
                if attempt == MAX_REPAIRS or not self.ledger.affordable(1):
                    return None
                text = self.ask(
                    request_base + attempt + 1,
                    prompts.FIX.format(error=rejection.detail_json[:3000], code=source),
                    [self.reference],
                    REPAIR_EFFORT,
                    MAX_OUTPUT_TOKENS,
                )
                source = extract_code(text)
        return None

    # ---- pipeline ----------------------------------------------------------------------------

    def run(self) -> str | None:
        chains = [Chain(k) for k in range(CHAINS)]

        # Stage 0: independent generations (request ids k*100 + 0..).
        texts = self.parallel([
            (lambda k=k: self.ask(k * 100, prompts.GENERATE, [self.reference], GEN_EFFORT, MAX_OUTPUT_TOKENS))
            for k in range(CHAINS)
        ])
        for chain, text in zip(chains, texts, strict=True):
            cand = self.realize(chain.index * 100 + 10, extract_code(text))
            if cand:
                chain.history.append(cand)

        # Refinement rounds (request ids k*100 + 20*r ..).
        for r in range(1, REFINE_ROUNDS + 1):
            active = [c for c in chains if c.history]
            if not active or not self.ledger.affordable(len(active)):
                break
            texts = self.parallel([
                (lambda c=c: self.ask(
                    c.index * 100 + 20 * r,
                    prompts.REFINE.format(code=c.history[-1].source),
                    [self.reference, data_url(c.history[-1].front), data_url(c.history[-1].sides)],
                    REFINE_EFFORT,
                    MAX_OUTPUT_TOKENS,
                ))
                for c in active
            ])
            for chain, text in zip(active, texts, strict=True):
                if text is None:
                    continue
                m = PENALTY_RE.search(text)
                if m:
                    chain.history[-1].penalty = float(m.group(1))
                cand = self.realize(chain.index * 100 + 20 * r + 10, extract_code(text))
                if cand:
                    chain.history.append(cand)

        # Per-chain choice: best self-rated earlier version vs the (unrated) last version.
        finalists = [c for c in (self.choose_in_chain(ch) for ch in chains) if c is not None]
        if not finalists:
            return None
        if len(finalists) == 1:
            return finalists[0].source
        best = self.select(900, finalists)
        return (best or finalists[0]).source

    def choose_in_chain(self, chain: Chain) -> Candidate | None:
        h = chain.history
        if not h:
            return None
        if len(h) == 1:
            return h[0]
        rated = [(c.penalty, k) for k, c in enumerate(h) if c.penalty is not None]
        best_rated = min(rated)[1] if rated else 0
        last = len(h) - 1
        if best_rated == last:
            return h[last]
        choice = self.select(chain.index * 100 + 90, [h[best_rated], h[last]])
        return choice or h[last]

    def select(self, request_id: int, cands: list[Candidate]) -> Candidate | None:
        if not self.ledger.affordable(1):
            return None
        letters = "ABCDEFGH"[: len(cands)]
        labels = "\n".join(f"Candidate {letters[i]}: image {i + 2}" for i in range(len(cands)))
        text = self.ask(request_id, prompts.SELECT.format(labels=labels),
                        [self.reference] + [data_url(c.front) for c in cands], SELECT_EFFORT,
                        SELECT_MAX_OUTPUT_TOKENS)
        if text:
            m = BEST_RE.search(text)
            if m and m.group(1) in letters:
                return cands[letters.index(m.group(1))]
        return None


def run(image: bytes, seed: int, calls: ModelCalls, renderer: Renderer) -> str | None:
    del seed  # nothing in this pipeline is random
    return Miner(image, calls, renderer).run()
