"""Draws the figures in docs/figures as SVG, each in a light and a dark version.

Run `uv run python docs/figures/figures.py` after changing a figure. The README and the ADRs
embed both versions with <picture>, so each reader sees the one that matches their theme.
The blocks keep the same colours in both versions; each colour means one kind of step (the
legend under each figure)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FONT = "Helvetica Neue, Helvetica, Arial, Liberation Sans, sans-serif"

# Each kind of block: fill and outline. The same in both themes.
KINDS: dict[str, tuple[str, str]] = {
    "code": ("#cfe6f5", "#4f86b0"),  # deterministic code
    "model": ("#fbd9ae", "#c98330"),  # a model's judgement
    "check": ("#f3efb5", "#a89a2c"),  # a check the kernel enforces
    "personal": ("#f6d0d4", "#bf6672"),  # personal data, sealed
    "person": ("#cdeac5", "#559b4c"),  # a person
    "computed": ("#dcd8f3", "#6f66b3"),  # computed from the log
    "data": ("#ffffff", "#8a8f98"),  # what comes in or goes out
}

LEGEND = [
    ("code", "deterministic code"),
    ("model", "a model's judgement"),
    ("check", "a kernel check"),
    ("personal", "sealed personal data"),
    ("person", "a person"),
    ("computed", "computed"),
]


@dataclass(frozen=True)
class Theme:
    suffix: str
    background: str
    panel: str
    panel_line: str
    text: str
    muted: str
    arrow: str
    block_text: str = "#1f2328"


LIGHT = Theme("", "#ffffff", "#f2f3f5", "#a3a9b1", "#1f2328", "#59636e", "#3d444d")
DARK = Theme("-dark", "#0d1117", "#161b22", "#3d444d", "#e6edf3", "#9198a1", "#c9d1d9")


class Figure:
    """An SVG drawn in one theme: rounded blocks, panels, cylinders, arrows and labels."""

    def __init__(self, theme: Theme, width: int, height: int, title: str) -> None:
        self.t = theme
        self.width = width
        self.height = height
        self.title = title
        self.parts: list[str] = []

    def panel(self, x: float, y: float, w: float, h: float, label: str = "") -> None:
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{self.t.panel}" '
            f'stroke="{self.t.panel_line}" stroke-width="1.5"/>'
        )
        if label:
            self.text(x + w / 2, y + 22, label, 13.5, bold=True, color=self.t.muted)

    def block(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        kind: str,
        title: str,
        lines: Sequence[str] = (),
        *,
        dashed: bool = False,
        size: float = 15.5,
    ) -> None:
        fill, line = KINDS[kind]
        # Not built yet: dashed and faded, so it reads on either background.
        dash = ' stroke-dasharray="6 4" fill-opacity="0.65"' if dashed else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
            f'stroke="{line}" stroke-width="1.6"{dash}/>'
        )
        self._lines(x + w / 2, y, h, title, lines, size)

    def cylinder(
        self, x: float, y: float, w: float, h: float, kind: str, title: str, lines: Sequence[str] = ()
    ) -> None:
        fill, line = KINDS[kind]
        ry = 9
        rx = w / 2
        body = (
            f"M{x} {y + ry} A{rx} {ry} 0 0 1 {x + w} {y + ry} V{y + h - ry} "
            f"A{rx} {ry} 0 0 1 {x} {y + h - ry} Z"
        )
        self.parts.append(f'<path d="{body}" fill="{fill}" stroke="{line}" stroke-width="1.6"/>')
        self.parts.append(
            f'<path d="M{x} {y + ry} A{rx} {ry} 0 0 0 {x + w} {y + ry}" fill="none" '
            f'stroke="{line}" stroke-width="1.6"/>'
        )
        self._lines(x + w / 2, y + ry, h - ry, title, lines, 15.5)

    def _lines(self, cx: float, y: float, h: float, title: str, lines: Sequence[str], size: float) -> None:
        step = 16.5
        height = size + 3 + step * len(lines)
        base = y + (h - height) / 2 + size - 1
        self.text(cx, base, title, size, bold=True, color=self.t.block_text)
        for i, line in enumerate(lines):
            self.text(cx, base + 4 + step * (i + 1), line, 13, color=self.t.block_text)

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float,
        *,
        bold: bool = False,
        color: str | None = None,
        anchor: str = "middle",
        italic: bool = False,
        spacing: float = 0,
    ) -> None:
        weight = ' font-weight="700"' if bold else ""
        style = ' font-style="italic"' if italic else ""
        tracking = f' letter-spacing="{spacing}"' if spacing else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}"{weight}{style}{tracking} text-anchor="{anchor}" '
            f'fill="{color or self.t.text}">{escape(s)}</text>'
        )

    def pill(self, x: float, y: float, label: str) -> None:
        """A small tag centred on (x, y), for a block's status."""
        w = 14 + 6.6 * len(label)
        self.parts.append(
            f'<rect x="{x - w / 2}" y="{y - 9}" width="{w}" height="18" rx="9" fill="{self.t.background}" '
            f'stroke="{self.t.muted}" stroke-width="1.2"/>'
        )
        self.text(x, y + 4, label, 11.5, bold=True, color=self.t.muted)

    def bracket(self, x: float, y0: float, y1: float, label: str) -> None:
        """A vertical bracket with its label written upward beside it."""
        self.parts.append(
            f'<path d="M{x + 6} {y0} H{x} V{y1} H{x + 6}" fill="none" stroke="{self.t.muted}" '
            'stroke-width="1.4"/>'
        )
        mid = (y0 + y1) / 2
        self.parts.append(
            f'<text x="{x - 7}" y="{mid}" font-size="12.5" font-style="italic" text-anchor="middle" '
            f'fill="{self.t.muted}" transform="rotate(-90 {x - 7} {mid})">{escape(label)}</text>'
        )

    def arrow(self, d: str, *, dashed: bool = False, head: bool = True) -> None:
        """A path with an arrowhead at its end; without `head`, a line joining another arrow."""
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        marker = ' marker-end="url(#head)"' if head else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{self.t.arrow}" stroke-width="1.8"{dash}{marker}/>'
        )

    def legend(self, y: float, extra: Sequence[tuple[str, str]] = ()) -> None:
        """One row of swatches, centred: the kinds, then any dashed meanings in `extra`."""
        items = [(kind, label, False) for kind, label in LEGEND] + [
            ("data", label, True) for _, label in extra
        ]
        widths = [27 + 6.3 * len(label) for _, label, _ in items]
        x = (self.width - sum(widths) - 18 * (len(items) - 1)) / 2
        for (kind, label, dashed), w in zip(items, widths, strict=True):
            fill, line = KINDS[kind]
            dash = ' stroke-dasharray="4 3"' if dashed else ""
            if dashed:
                fill = "none"
                line = self.t.muted
            self.parts.append(
                f'<rect x="{x}" y="{y - 12}" width="20" height="14" rx="4" fill="{fill}" '
                f'stroke="{line}" stroke-width="1.4"{dash}/>'
            )
            self.text(x + 27, y, label, 12.5, color=self.t.muted, anchor="start")
            x += w + 18

    def svg(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}" '
            f'width="{self.width}" height="{self.height}" font-family="{FONT}" role="img">\n'
            f"<title>{escape(self.title)}</title>\n"
            "<defs>"
            '<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{self.t.arrow}"/></marker>'
            "</defs>\n"
            f'<rect width="{self.width}" height="{self.height}" rx="14" fill="{self.t.background}"/>\n'
        )
        return head + "\n".join(self.parts) + "\n</svg>\n"


def architecture(t: Theme) -> str:
    """Figure 1: encoders turn what is sensed into claims, the kernel validates and logs them,
    decoders read at one offset and cite, and the loop closes only through a person."""
    f = Figure(t, 1090, 990, "World Model Kernel: encoders, the kernel and decoders")
    enc, ker, dec, col = 40, 390, 740, 300
    top, bottom = 88, 768

    for x, name, note in (
        (enc, "ENCODERS", "turn what is sensed into sourced claims"),
        (ker, "KERNEL", "one write path, one log, computed belief"),
        (dec, "DECODERS", "read at one offset, write nothing but cites"),
    ):
        f.text(x + col / 2, 44, name, 19, bold=True, spacing=2.5)
        f.text(x + col / 2, 66, note, 13.5, color=t.muted)
        f.panel(x, top, col, bottom - top)

    # Encoders, read upward: from the world to a claim and the operations it justifies.
    ix, iw, cx = enc + 16, col - 32, enc + col / 2
    f.block(ix, 108, iw, 68, "data", "Claim + graph operations", ["text · source · quote · basis · ops"])
    f.block(
        ix,
        200,
        iw,
        76,
        "check",
        "Extraction run",
        ["closing it retracts what older", "runs found and it did not"],
    )
    half = (iw - 10) / 2
    f.block(
        ix,
        300,
        half,
        132,
        "code",
        "Deterministic",
        ["reader: exports,", "event logs, repos,", "stacks, forges", "→ observed claims"],
    )
    f.block(
        ix + half + 10,
        300,
        half,
        132,
        "model",
        "Model + skills",
        ["a person reviews", "each claim", "→ reported claims", "that quote"],
    )
    f.block(
        ix,
        456,
        iw,
        76,
        "personal",
        "Store each source",
        ["chunks with spans, content hash;", "sealed under its people's keys"],
    )
    f.block(
        ix,
        556,
        iw,
        76,
        "person",
        "Consent",
        ["each person recorded is created", "by the claim of their consent"],
    )
    f.block(
        ix, 656, iw, 76, "code", "Convert at the edge", ["PDF, Word → text: markitdown,", "docling, GROBID"]
    )
    for y0, y1 in ((656, 632), (556, 532)):
        f.arrow(f"M{cx} {y0} V{y1 + 2}")
    lx, rx = ix + half / 2, ix + half + 10 + half / 2
    f.arrow(f"M{cx} 456 V444 H{lx} V434")
    f.arrow(f"M{cx} 444 H{rx} V434")
    f.arrow(f"M{lx} 300 V288 H{cx} V278")
    f.arrow(f"M{rx} 300 V288 H{cx}", head=False)
    f.arrow(f"M{cx} 200 V178")

    # The kernel, read downward: the gateway, kernel.write's steps, the log, the graph, belief.
    kx, kw, kc = ker + 16, col - 32, ker + col / 2
    f.block(
        kx, 108, kw, 68, "code", "MCP gateway, any harness", ["write · write_batch · ingest_source · cite"]
    )
    f.arrow(f"M{ix + iw} 142 H{kx - 2}")
    f.panel(kx, 198, kw, 290, "kernel.write: one transaction, in order")
    steps = [
        ("check", "Lock, take the next offset"),
        ("check", "Provenance: source, quote, run"),
        ("check", "Ontology and governance rules"),
        ("check", "Stale check: read_at_offset"),
        ("code", "Resolution cascade"),
        ("personal", "Seal personal data"),
    ]
    for i, (kind, title) in enumerate(steps):
        f.block(kx + 12, 232 + 41 * i, kw - 24, 34, kind, title, size=14.5)
    f.arrow(f"M{kc} 176 V196")
    f.cylinder(kx + 20, 510, kw - 40, 64, "data", "Log", ["append-only · the source of truth"])
    f.cylinder(kx + 20, 592, kw - 40, 64, "computed", "Graph", ["its projection; replay rebuilds it"])
    f.arrow(f"M{kc} 488 V508")
    f.arrow(f"M{kc} 574 V590")
    f.block(
        kx,
        676,
        kw,
        76,
        "computed",
        "Belief",
        ["from independent origins: accepted,", "contested, rejected or unknown"],
    )
    f.arrow(f"M{kc} 656 V674")

    # Decoders, read upward: a read at one offset to findings that cite their sources.
    dx, dw, dc = dec + 16, col - 32, dec + col / 2
    rows = [
        (
            "data",
            "Findings with receipts",
            ["discovery report · controls test ·", "audit evidence · the explorer"],
        ),
        ("check", "Cite", ["each finding → its claims →", "their sources' words"]),
        ("personal", "Erased stays erased", ["attributed to collections,", "never to names"]),
        ("code", "Decode", ["compare · rank · drift ·", "forge audit · report"]),
        ("code", "Read at one log offset", ["query_graph · query_log ·", "lookup_entities"]),
    ]
    ys = [108, 250, 392, 534, 676]
    for (kind, title, lines), y in zip(rows, ys, strict=True):
        f.block(dx, y, dw, 76, kind, title, lines)
    for upper, lower in pairwise(ys):
        f.arrow(f"M{dc} {lower} V{upper + 78}")
    f.arrow(f"M{kx + kw} 714 H{dx - 2}")

    # The loop closes only through a person: proposal, decision, actuator, the world.
    f.block(
        enc,
        812,
        col,
        82,
        "data",
        "The world",
        ["documents · transcripts · exports ·", "repositories · running stacks · papers"],
    )
    f.block(840, 812, 200, 82, "model", "Proposal", ["a change an agent drafts", "from what it read"])
    f.block(
        615, 812, 200, 82, "person", "A person decides", ["signed in, never the", "proposer: kernel.decide"]
    )
    f.block(
        390,
        812,
        200,
        82,
        "code",
        "Actuator",
        ["acts only on an approved", "change (not built yet)"],
        dashed=True,
    )
    f.arrow(f"M{dx + dw} 146 H1066 V853 H1042")
    f.arrow("M840 853 H817")
    f.arrow("M615 853 H592")
    f.arrow(f"M390 853 H{enc + col + 2}", dashed=True)
    f.arrow(f"M{cx} 812 V734")
    f.legend(950, [("", "not built yet")])
    return f.svg()


def trajectory(t: Theme) -> str:
    """Figure 2 (ADR 0037): the entity is its log; recall, a loop, expectation and learning are
    built around the kernel, and vessels lend it senses and effectors at three timescales."""
    f = Figure(t, 1090, 940, "Toward a cognitive model: the entity, its learning and its vessels")
    left, mid, right = (30, 260), (320, 440), (800, 260)
    for (x, w), name, note in (
        (left, "TIMESCALES", "fast layers stay in the vessel"),
        (mid, "THE ENTITY", "one log, many vessels, acting only as approved"),
        (right, "LEARNING", "every change it wants is a proposal"),
    ):
        f.text(x + w / 2, 44, name, 19, bold=True, spacing=2.5)
        f.text(x + w / 2, 66, note, 13.5, color=t.muted)
    f.panel(left[0], 88, left[1], 782)
    f.panel(mid[0], 88, mid[1], 782)
    f.panel(right[0], 88, right[1], 452)

    # The entity, read upward from its vessels: sense, remember, recall, deliberate, decide.
    bx, bw = 362, 382
    cx = bx + bw / 2
    f.block(
        bx,
        112,
        bw,
        72,
        "person",
        "Decide",
        ["a person approves each proposal; later,", "also policies people approved (stage 6)"],
    )
    f.pill(bx + bw - 40, 112, "built")
    f.block(
        bx,
        212,
        bw,
        72,
        "model",
        "Deliberate",
        ["the loop: wakes on what is new, recalls,", "proposes; people write its goals"],
        dashed=True,
    )
    f.pill(bx + bw - 44, 212, "stage 2")
    f.block(
        bx,
        312,
        bw,
        72,
        "code",
        "Recall",
        ["working memory: what a task needs, with", "belief, quotes and the read offset"],
        dashed=True,
    )
    f.pill(bx + bw - 44, 312, "stage 1")
    f.block(
        bx,
        412,
        bw,
        104,
        "computed",
        "Memory: the kernel",
        [
            "episodic log · semantic graph · belief",
            "two clocks · self boundary · erasure",
            "lived and implanted memories apart",
        ],
    )
    f.pill(bx + bw - 40, 412, "built")
    half = (bw - 12) / 2
    f.block(
        bx, 544, half, 72, "code", "Actuators", ["effectors: act only", "on approved changes"], dashed=True
    )
    f.pill(bx + half - 46, 544, "Phase 3")
    f.block(bx + half + 12, 544, half, 72, "code", "Encoders", ["senses: observed and", "reported claims"])
    f.pill(bx + bw - 40, 544, "built")
    for y0, y1 in ((412, 384), (312, 284), (212, 184)):
        f.arrow(f"M{cx} {y0} V{y1 + 2}")
    ex = bx + half + 12 + half / 2
    ax = bx + half / 2
    f.arrow(f"M{ex} 544 V518")
    f.arrow(f"M{bx} 148 H346 V530 H{ax} V542")

    # Vessels: bodies it senses and acts through, digital and physical.
    vy, vw = 648, (bw - 12) / 2
    for i, (name, chips) in enumerate(
        (
            (
                "Digital",
                [
                    ("its own stack", False),
                    ("repositories, forge", False),
                    ("conversation", False),
                    ("workflow runtimes", True),
                    ("browser, desktop", True),
                ],
            ),
            (
                "Physical",
                [
                    ("simulation first", True),
                    ("robots, through ROS 2", True),
                    ("buildings", True),
                    ("vehicles", True),
                ],
            ),
        )
    ):
        x = bx + i * (vw + 12)
        f.parts.append(
            f'<rect x="{x}" y="{vy}" width="{vw}" height="206" rx="12" fill="none" '
            f'stroke="{t.panel_line}" stroke-width="1.4"/>'
        )
        f.text(x + vw / 2, vy + 22, name, 14.5, bold=True)
        for j, (chip, later) in enumerate(chips):
            f.block(x + 12, vy + 34 + 34 * j, vw - 24, 28, "data", chip, dashed=later, size=13)
    f.arrow(f"M{ex} {vy} V618")
    f.arrow(f"M{ax} 616 V{vy - 2}", dashed=True)

    # Timescales: what runs how fast, and where it is remembered.
    lx, lw = 74, 204
    f.text(left[0] + left[1] / 2, 132, "Each layer keeps its own pace;", 13, color=t.muted, italic=True)
    f.text(left[0] + left[1] / 2, 150, "the log records the slow ones,", 13, color=t.muted, italic=True)
    f.text(left[0] + left[1] / 2, 168, "never raw samples.", 13, color=t.muted, italic=True)
    f.block(lx, 212, lw, 72, "model", "Deliberation", ["seconds to days: the loop,", "the kernel, models"])
    f.block(
        lx,
        412,
        lw,
        104,
        "computed",
        "One stream",
        ["every vessel writes one log,", "in one order: what it lived,", "and when"],
    )
    f.block(
        lx, 544, lw, 72, "code", "Routine", ["seconds to minutes: compiled", "workflows, behaviour trees"]
    )
    f.block(lx, 660, lw, 72, "code", "Reflex", ["milliseconds: the vessel's", "controller, in its envelope"])
    f.block(lx, 770, lw, 72, "data", "Safety", ["stops and limits the", "entity can never change"])
    f.bracket(58, 212, 616, "in the log")
    f.bracket(58, 660, 842, "in the vessel")

    # Learning: instruments judge; research, concepts, habits and expectation propose.
    rx, rw = 816, 228
    f.block(rx, 112, rw, 72, "person", "Instruments", ["evals, rules, envelopes:", "two people approve each"])
    f.pill(rx + rw - 40, 112, "built")
    f.block(
        rx,
        212,
        rw,
        72,
        "model",
        "Improve from research",
        ["papers → experiments in", "forks → evals → PRs"],
        dashed=True,
    )
    f.pill(rx + rw - 44, 212, "stage 5")
    f.block(
        rx,
        312,
        rw,
        72,
        "code",
        "Concepts and habits",
        ["unresolved claims → schema;", "its own runs → procedures"],
        dashed=True,
    )
    f.pill(rx + rw - 44, 312, "stage 4")
    f.block(
        rx,
        412,
        rw,
        104,
        "computed",
        "Expectation",
        ["predictions settled by", "what is observed:", "calibration per source"],
        dashed=True,
    )
    f.pill(rx + rw - 44, 412, "stage 3")
    f.arrow(f"M{rx} 148 H{bx + bw + 2}")
    f.arrow(f"M{rx} 248 H{bx + bw + 2}")
    f.arrow(f"M{bx + bw} 464 H{rx - 2}")
    f.arrow(f"M{rx + rw / 2} 412 V386")
    f.arrow(f"M{rx + rw / 2} 312 V286")

    # The stages, in order.
    f.panel(right[0], 572, right[1], 298)
    f.text(right[0] + right[1] / 2, 600, "Stages (ADR 0037)", 14.5, bold=True)
    for i, line in enumerate(
        [
            "built: memory, senses, approval",
            "Phase 3: the first actuators",
            "1  recall",
            "2  the loop",
            "3  expectation",
            "4  concepts and habits",
            "5  improving from research",
            "6  digital vessels",
            "7  physical vessels",
        ]
    ):
        f.text(right[0] + 30, 630 + 26 * i, line, 13.5, anchor="start", color=t.text)
    f.legend(905, [("", "not built yet")])
    return f.svg()


def main() -> None:
    for theme in (LIGHT, DARK):
        (HERE / f"architecture{theme.suffix}.svg").write_text(architecture(theme))
        (HERE / f"trajectory{theme.suffix}.svg").write_text(trajectory(theme))


if __name__ == "__main__":
    main()
