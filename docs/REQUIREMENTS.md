# Requirements

The risk engine owns a subset of the ABS system requirements, defined in [SYSTEM_REQUIREMENTS.md](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md). Those it owns are referenced against the functional and non-functional requirements below.

## Functional

| ID | Requirement | ABS |
|----|-------------|-----|
| REQ-F-001 | The system shall return a decision of allow, review or block for a payment presented to `POST /risk/evaluate`. | |
| REQ-F-002 | The system shall compute a 0 to 100 score as the sum of the weights of the rules a payment fires, capped at 100. | |
| REQ-F-003 | The system shall map the score to a decision using configurable band boundaries (allow, review, block). | |
| REQ-F-004 | The system shall attach to every decision the rules that fired and their weights, so the decision is explainable. | ABS-REQ-015 |
| REQ-F-005 | The system shall record for every decision the input snapshot, the rule version, the triggered rules and weights, the score, the decision, the correlation_id and the timestamp. | ABS-REQ-015 |
| REQ-F-006 | The system shall echo the caller's correlation_id on the decision so a request is traceable across services. | ABS-REQ-009 |
| REQ-F-007 | The system shall maintain per-account behavioural state (velocity, destinations seen, recent failures, amount history, first-seen time) from consumed events. | |
| REQ-F-008 | The system shall compute a decision from the behavioural state and the payment at evaluate time, without contacting any other service. | ABS-REQ-016 |
| REQ-F-009 | The system shall emit a domain event for every decision through a transactional outbox. | ABS-REQ-007 |
| REQ-F-010 | The system shall expose a health endpoint that probes the database. | |

## Non-Functional

| ID | Requirement | ABS |
|----|-------------|-----|
| REQ-NF-001 | A decision shall be deterministic and replayable: the same input snapshot and rule version always produce the same score and decision. | ABS-REQ-014 |
| REQ-NF-002 | Uncertainty shall never permit money to move: the engine never returns a decision that lets an unscored payment proceed, and the caller holds a payment for review when the engine is unavailable. | ABS-REQ-013 |
| REQ-NF-003 | The synchronous decision path shall not depend on the asynchronous state feed being healthy or current; a decision is returned even if event consumption has stopped. | ABS-REQ-016 |
| REQ-NF-004 | The engine shall not read or write financial state; it never moves money or records a ledger transaction. | ABS-REQ-001 |
| REQ-NF-005 | Events shall be delivered at least once, captured atomically with the decision, and deduplicated by consumers on event_id. | ABS-REQ-007, 008 |
| REQ-NF-006 | Rule weights and thresholds shall be configuration, and a change to them is a new rule version so past decisions remain replayable. | ABS-REQ-014 |
| REQ-NF-007 | Monetary values shall use Decimal (Python) and Numeric (PostgreSQL). No floats. | |
| REQ-NF-008 | Credentials shall be stored in Secret Manager and injected at runtime, never in source control. | |
| REQ-NF-009 | The CI pipeline shall lint, run the full test suite against PostgreSQL, and validate the Terraform before deploy. | |
| REQ-NF-010 | Schema changes shall be managed through versioned Alembic migrations. | |
