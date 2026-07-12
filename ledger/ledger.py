"""The design ledger: every non-obvious decision and live hypothesis, as a checkable tree.

Not a changelog. Each decision carries its rationale and the alternatives it rejected; each
hypothesis carries its own falsification criterion — so the ledger can be *checked* (see
tests/test_ledger.py), not merely read. A decision whose stated rationale no longer holds
should be discoverable by a rule, not by memory.

Authored against `bom` (the ledger substrate: kinds, rules and solvers as data). bom is a
private library, so it is NOT a dependency of this public package — the ledger check simply
skips where bom is not installed, and runs on machines that have it.
"""

from bom import Bom, KindDef, Node, Rule

VOCABULARY = [
    KindDef(kind="decision",
            description="A non-obvious design decision. Rationale in meta['rationale']; every "
                        "alternative genuinely weighed is a rejected-alternative child."),
    KindDef(kind="rejected-alternative",
            description="A road not taken, with why in meta['why_not']. Its presence is what "
                        "separates a decision from a default."),
    KindDef(kind="hypothesis",
            description="A belief this design leans on but has not proven. Carries its own "
                        "falsification criterion as a falsifier child."),
    KindDef(kind="falsifier",
            description="The observation that would kill the parent hypothesis, in "
                        "meta['observation']. A hypothesis without one is faith."),
]

RULES = [
    Rule(name="a-decision-weighs-alternatives", kind="decision",
         description="A decision with no rejected alternative is a default wearing a hat.",
         expr="len(nodes('rejected-alternative', self)) >= 1"),
    Rule(name="a-hypothesis-is-falsifiable", kind="hypothesis",
         description="Every hypothesis states what observation would kill it.",
         expr="len(nodes('falsifier', self)) >= 1"),
]

LEDGER = Bom(
    vocabulary=VOCABULARY,
    rules=RULES,
    root=Node(id="root", name="repolock design ledger", children=[

        Node(id="protocol-not-process", kind="decision", name="Ship a protocol, not a service",
             meta={"rationale":
                   "The enforcement half of a lock cannot be an MCP tool: a tool is something "
                   "the model chooses to call, and the offending session never chooses to. "
                   "Binding enforcement is vendor-specific (each harness's hook mechanism); "
                   "what is portable is the lockfile convention. So the deliverable is SPEC.md "
                   "plus a reference library plus per-harness adapters."},
             children=[
                 Node(id="alt-mcp-only", kind="rejected-alternative", name="MCP tool only",
                      meta={"why_not": "advisory by construction — the session that needs "
                                       "stopping never calls it"}),
                 Node(id="alt-daemon", kind="rejected-alternative", name="A lock daemon/service",
                      meta={"why_not": "something to install, supervise and keep alive on every "
                                       "machine; the hook renews on activity so no process needs "
                                       "to outlive the call"}),
             ]),

        Node(id="core-zero-deps", kind="decision", name="Core is pure stdlib; extras for the rest",
             meta={"rationale":
                   "A lock convention with a dependency tree is an adoption blocker. "
                   "flight-recorder and mcp are optional extras, imported only at the moment "
                   "they are used; recording off means zero non-stdlib imports, enforced by "
                   "test_the_core_imports_no_optional_dependency."},
             children=[
                 Node(id="alt-hard-dep", kind="rejected-alternative",
                      name="Hard dependency on flight-recorder",
                      meta={"why_not": "a personal lib on the install path of every adopter; "
                                       "also a per-write import tax in the hook's hot path"}),
             ]),

        Node(id="liveness-lease-and-pid", kind="decision",
             name="Liveness = unexpired lease AND live holder; pid<=0 degrades to lease-only",
             meta={"rationale":
                   "Lease alone lets a crashed session block others until it runs out; PID "
                   "alone lets an idle session hold the repo for hours. Hook-taken locks have "
                   "no usable PID (the hook process exits immediately), so pid<=0 must read as "
                   "'cannot disprove liveness' or every hook lock would be stolen on sight."},
             children=[
                 Node(id="alt-lease-only", kind="rejected-alternative", name="Lease-only",
                      meta={"why_not": "a crashed holder blocks for the whole lease even when "
                                       "the OS could disprove it in one call"}),
                 Node(id="alt-pid-only", kind="rejected-alternative", name="PID-only",
                      meta={"why_not": "an idle-at-lunch session holds forever; and hook locks "
                                       "have no PID at all"}),
             ]),

        Node(id="lockdir-outside-repo", kind="decision",
             name="Lockfiles live outside every repo (~/.repolock/locks)",
             meta={"rationale":
                   "A lock must work for a checkout that is not a git repo yet, and must never "
                   "appear in git status as an edit of its own. Path identity is canonical "
                   "(realpath+normcase) and hashed into the filename."},
             children=[
                 Node(id="alt-inrepo", kind="rejected-alternative",
                      name="A lockfile inside the repo (like .git/index.lock)",
                      meta={"why_not": "shows up as a working-tree change, needs the repo to "
                                       "exist, and dies with the checkout it guards"}),
             ]),

        Node(id="client-consumes-via-config", kind="decision",
             name="Clients consume the lock via machine config, not imports",
             meta={"rationale":
                   "The extraction's client kept no lock imports — its 'dependency' is the "
                   "hooks block and MCP registration on the machine, plus a transitional shim "
                   "so sessions holding the old hook snapshot keep working. The lib knows "
                   "nothing of any client, per the lib/client rule."},
             children=[
                 Node(id="alt-lib-import", kind="rejected-alternative",
                      name="Make the old host import the lib anyway",
                      meta={"why_not": "an unused import kept only to satisfy a sentence in an "
                                       "issue; the honest wiring is config"}),
             ]),

        # Positioning statements (e.g. the README's "planned obsolescence" line) do not belong
        # here: nothing in the design leans on them and no rule or tape can falsify them. The
        # ledger holds only what gates future work.

        Node(id="hyp-renewal-on-activity", kind="hypothesis",
             name="Renew-on-tool-call keeps live sessions from lapsing mid-work",
             meta={"claim": "A 600s hook lease outlasts the longest single tool call, so an "
                            "active session never loses its lock between calls."},
             children=[
                 Node(id="kill-renewal", kind="falsifier",
                      meta={"observation":
                            "A flight recording shows a lease lapsing between two tool calls "
                            "of one still-active session (a single call longer than the lease)."}),
             ]),
    ]),
)
