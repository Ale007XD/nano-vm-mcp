nano-vm-mcp

<p align="center">
  <a href="https://github.com/Ale007XD/nano-vm-mcp/actions">
    <img src="https://github.com/Ale007XD/nano-vm-mcp/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://pypi.org/project/nano-vm-mcp/">
    <img src="https://img.shields.io/pypi/v/nano-vm-mcp" alt="PyPI">
  </a>
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP">
</p><p align="center">
  <strong>Deterministic execution gateway for nano-vm.</strong><br>
  Stateful MCP orchestration with replayable execution traces.<br>
  LLM support is optional.
</p><p align="center">
  <em>MCP-native execution runtime for deterministic workflows and AI systems.</em>
</p>
---

What nano-vm-mcp Is

nano-vm-mcp is a stateful MCP gateway built on top of nano-vm.

It exposes deterministic execution workflows through the <Model Context Protocol> ecosystem.

The gateway provides:

deterministic workflow execution

replayable traces

suspend/resume orchestration

governance enforcement

capability-based tool access

append-only audit trails

MCP-native workflow APIs


LLMs are optional.

The deterministic FSM runtime remains the execution authority.


---

Architecture

MCP Client
    ↓
nano-vm-mcp
    ↓
Execution Gateway
    ↓
nano-vm (FSM Runtime)
    ↓
Trace + Governance Store

Or formally:

external events / tools / LLMs
                ↓
         nano-vm-mcp
                ↓
         deterministic FSM
                ↓
       replayable audit trail


---

Core Principle

The gateway does not control execution logic.

The runtime does.

Canonical invariant:

\delta(S,E) \rightarrow S'

Where:

 — current execution state

 — validated event

 — next deterministic state



---

Why nano-vm-mcp Exists

Most MCP servers expose tools.

nano-vm-mcp exposes deterministic execution.

This matters because:

tools alone are stateless

workflows require state

async processes require suspend/resume

enterprise systems require auditability

AI systems require governance boundaries


The gateway exists to provide:

execution correctness

replayability

governance enforcement

deterministic orchestration

stateful execution over MCP



---

What Makes It Different

Capability	Typical MCP Server	nano-vm-mcp

Tool execution	✅	✅
Stateful execution	❌	✅
Deterministic FSM	❌	✅
Replayable traces	❌	✅
Suspend/resume	❌	✅
Governance layer	❌	✅
Capability enforcement	❌	✅
Audit trail	partial	append-only



---

Install

pip install nano-vm-mcp

Optional LiteLLM support:

pip install "nano-vm-mcp[litellm]"


---

Quick Start

stdio transport

nano-vm-mcp --transport stdio

Claude Desktop config:

{
  "mcpServers": {
    "nano-vm-mcp": {
      "command": "nano-vm-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}


---

SSE Transport

NANO_VM_MCP_API_KEY=secret-token \
nano-vm-mcp --transport sse --port 8080

MCP endpoint:

http://localhost:8080/sse

Authorization header:

Authorization: Bearer secret-token


---

Docker Compose

services:
  nano-vm-mcp:
    image: ghcr.io/ale007xd/nano-vm-mcp:latest

    ports:
      - "8080:8080"

    volumes:
      - ./data:/data

    environment:
      NANO_VM_MCP_DB: /data/nano_vm_mcp.db
      NANO_VM_MCP_PORT: 8080
      NANO_VM_MCP_API_KEY: your-secret-token

    command:
      [
        "nano-vm-mcp",
        "--transport",
        "sse"
      ]


---

MCP Tools

Tool	Purpose

run_program	execute deterministic workflow
get_trace	retrieve full execution trace
list_programs	list stored programs
get_program	retrieve workflow definition
delete_program	delete workflow + traces



---

Using Without LLMs

LLMs are not required.

nano-vm-mcp can orchestrate pure business workflows.


---

Example — Payment Pipeline

program = {
    "name": "payment_flow",
    "steps": [
        {
            "id": "reserve",
            "type": "tool",
            "tool": "reserve_funds"
        },
        {
            "id": "capture",
            "type": "tool",
            "tool": "capture_payment"
        },
        {
            "id": "receipt",
            "type": "tool",
            "tool": "send_receipt"
        }
    ]
}

Execution properties:

deterministic ordering

replayable trace

exactly-once semantics

append-only audit trail


No LLM involved.


---

Example — Async Suspend / Resume

async def wait_bank_transfer(**kwargs):
    return "PENDING"

FSM lifecycle:

RUNNING → SUSPENDED → RUNNING → SUCCESS

This enables:

webhook orchestration

payment confirmation flows

human approvals

external event handling

long-running workflows



---

Example — Running a Workflow Through MCP

import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

program = {
    "name": "demo",
    "steps": [
        {
            "id": "step1",
            "type": "tool",
            "tool": "hello_tool"
        }
    ]
}

async def main():
    headers = {
        "Authorization": "Bearer your-secret-token"
    }

    async with sse_client(
        "http://localhost:8080/sse",
        headers=headers
    ) as (r, w):

        async with ClientSession(r, w) as session:
            await session.initialize()

            result = await session.call_tool(
                "run_program",
                {
                    "program": program,
                    "save_as": "demo"
                }
            )

            print(result.content[0].text)

asyncio.run(main())


---

Deterministic Execution Guarantees

Guarantee	nano-vm-mcp

Replayable traces	✅
Deterministic transitions	✅
Exactly-once execution	✅
Suspend/resume	✅
Auditability	✅
Capability enforcement	✅
Governance enforcement	✅



---

Governance Layer

Every successful execution step produces a GovernanceEnvelope.

The envelope is immutable.

The audit trail is append-only.


---

GovernanceEnvelope

Each successful transition stores:

Field	Description

execution_id	workflow execution id
step_id	execution step
policy_hash	active policy fingerprint
canonical_snapshot_hash	deterministic state hash
payload	projected sanitized output


This creates a tamper-evident execution history.


---

CapabilityRef and GDPR Tombstoning

Sensitive values are stored as capability references:

vault://secret/<id>

On erasure:

E_gdpr_erase → tombstone

Projected values become:

[REDACTED_TOMBSTONE]

The hash chain remains valid.

The secret disappears.


---

Security

AST-safe Conditions

Condition expressions are evaluated through the deterministic AST engine.

eval() is never used.

Supported operators:

==

!=

>

<

in

not in

and

or

not



---

Capability Enforcement

GovernedToolExecutor validates tool access against the active policy snapshot.

Unauthorized tools are rejected before execution.

Two enforcement layers exist:

Layer	Responsibility

ExecutionVM	registered tool validation
GovernedToolExecutor	policy capability validation



---

Authentication

Enable bearer auth:

NANO_VM_MCP_API_KEY=your-secret-token

Timing-safe comparison:

secrets.compare_digest(...)

Never expose SSE publicly without authentication.


---

Configuration

Variable	Description

NANO_VM_MCP_DB	SQLite WAL database
NANO_VM_MCP_HOST	bind host
NANO_VM_MCP_PORT	bind port
NANO_VM_MCP_API_KEY	bearer token
NANO_VM_MCP_LLM_MODEL	optional LiteLLM model



---

Endpoints

Endpoint	Purpose

/health	liveness probe
/sse	MCP SSE transport
/messages	MCP message endpoint



---

State Model

The gateway itself is stateful.

Execution lifecycle:

CREATED
  ↓
RUNNING
  ↓
SUSPENDED
  ↓
RUNNING
  ↓
SUCCESS / FAILED

Terminal states are immutable.


---

Observability

Every execution exposes:

trace.trace_id
trace.status
trace.steps
trace.error
trace.state_snapshots

Execution becomes replayable and inspectable.


---

Relationship to nano-vm

Layer	Responsibility

nano-vm	deterministic execution kernel
nano-vm-mcp	stateful MCP gateway


The gateway never owns transition logic.

The FSM kernel does.


---

Philosophy

nano-vm-mcp is not a tool wrapper.

It is:

\text{Deterministic Stateful Execution Gateway}

The system exposes deterministic execution semantics over MCP.

Not just tools.


---

Comparison

	Typical MCP Server	nano-vm-mcp

Stateless tools	✅	✅
Stateful workflows	❌	✅
Suspend/resume	❌	✅
Governance layer	❌	✅
Replayability	❌	✅
Deterministic FSM	❌	✅
Audit trail	partial	append-only



---

Use Cases

nano-vm-mcp is designed for:

fintech orchestration

payment systems

governance-bound AI

enterprise automation

async approval workflows

webhook pipelines

deterministic AI execution

MCP-native workflow systems



---

Roadmap

Runtime

[x] Stateful MCP gateway

[x] SSE transport

[x] stdio transport

[x] SQLite WAL persistence

[x] Suspend/resume support

[x] Governance envelopes

[x] Capability enforcement


Infrastructure

[ ] Distributed gateway

[ ] REST execution API

[ ] Execution dashboard

[ ] Horizontal execution scaling


AI Layer

[ ] Planner integration

[ ] Dynamic policy routing

[ ] Multi-provider execution pools



---

Contact

GitHub:

[nano-vm-mcp GitHub Repository](https://github.com/Ale007XD/nano-vm-mcp?utm_source=chatgpt.com)

Kernel Runtime:

[nano-vm GitHub Repository](https://github.com/Ale007XD/nano_vm?utm_source=chatgpt.com)

PyPI:

[nano-vm-mcp on PyPI](https://pypi.org/project/nano-vm-mcp/?utm_source=chatgpt.com)


---

License

MIT License.
