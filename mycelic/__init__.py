"""Mycelic: local-first distributed organizational intelligence on top of NeuralGraph.

Package map (see docs/mycelic/ARCHITECTURE.md):

* ``mycelic.db``          coordination database (SQLite), migrations, event outbox
* ``mycelic.auth``        passwords, sessions, invitations, API keys
* ``mycelic.org``         tenants, organizational units, memberships, grants, holders registry
* ``mycelic.authz``       the one authorization engine every layer calls
* ``mycelic.evidence``    holder-side evidence store built on ``NeuralGraph.chat_memory``
* ``mycelic.transport``   durable artifact transport (SQLite outbox, NATS JetStream)
* ``mycelic.models``      model providers, tiered routing, usage ledger
* ``mycelic.knowledge``   claims, evidence refs, derivations, conflicts, revisions, discoveries, commit gate
* ``mycelic.goals``       goals and progress
* ``mycelic.inquiry``     QuestionArtifacts, prioritization, routing, evaluation
* ``mycelic.discovery``   the continual-discovery loop worker
* ``mycelic.agents``      per-employee / per-unit agents (cited chat)
* ``mycelic.api``         aiohttp application, SSE, metrics, health
* ``mycelic.holder``      standalone evidence-holder process
* ``mycelic.seed``        reproducible demonstration organization
"""

__version__ = "0.1.0"
