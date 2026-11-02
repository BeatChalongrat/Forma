# ADR 061: Round requirements and controlled evaluation

Round creation accepts bounded operator instructions, persisted in the existing ImportRound.notes column.
Schema, mapping and adaptation agents receive them as operator_requirements. Source cells remain untrusted
data and do not become operator instructions. The requirements hash and length are recorded in agent_log.
An approved mapping is not reused verbatim when its source round's requirements differ, even when headers
are identical. No schema fields or transformation rules are hard-coded by this change.

The international evaluation separates prediction before clarification from the later, controlled operator
answer. Predictions are read-only mapping previews; the harness does not approve or export them blindly.
Ground truth is read only by the evaluator. A fresh independent evaluator receives only a copied input/task/
schema package. It is a coding-agent comparison, not a claim about the ChatGPT or Claude product.

The initial evaluation uses isolated schema references/customers and records runtime model/configuration.
Score expected populated fields, identity multiplicity, collection extras and unsupported business facts;
do not inflate accuracy with absent fields or overwrite duplicate collection keys.
