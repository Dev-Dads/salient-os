"""SalienceOS — the control plane of an AI-governed operating system.

Destination (docs/ROADMAP-plain-language.md): a machine that boots straight
into SalienceOS. This package is its judgment system; hosts it governs are
bodies, and near-term host integrations are test rigs, not the product.

Build order (per SalienceOS_Design_Review_v0.2.md, Part 4):
1. verifier     — separate-process, side-effect-free evidence pipeline
2. bus + central interpreter — thin salience contract, one fail-closed choke point
3. control seam — directive + verdict composed into one GovernedOutcome
4. consumers    — the memory and weight-adaptation channels that OBEY it
"""
