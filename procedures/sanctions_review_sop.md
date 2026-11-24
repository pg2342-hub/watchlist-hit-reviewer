# Sanctions screening review: procedure for OFAC SDN name hits

A constructed procedure for a bank's financial crimes team, written for this backtest. The agent is given this text; the analysts' past calls in `evals/cases.yml` follow it.

## Scope
Name hits from screening a customer (an individual) against the OFAC SDN list at onboarding. Each hit is reviewed on its own; a customer with several hits needs a call on every one.

## Evidence
- The customer record: name, date of birth (DOB), nationality and address in the vault, plus what the ID document scan read (type, DOB, gender).
- The list entry: listed name and aliases, every listed DOB (some are a full date, some only a month or year, some "circa" a year or a range), place of birth, nationality, citizenship, gender, addresses, sanctions programs.
- Use the vault DOB if present, otherwise the DOB read from the ID document. Never assume a DOB that is not on file.

## Calls
1. **Escalate, likely match** when the name matches and the customer's full DOB equals any listed full DOB (not just the first one), or the customer's record otherwise points to the listed person.
2. **Clear, DOB** when the customer's DOB is on file and it differs from every listed DOB: a different full date, or a year outside a listed year, "circa" year (plus or minus one year) or range. One listed DOB that fits is enough to stop a clear.
3. **Clear, gender** when no listed DOB rules the customer out, but the ID document's gender differs from the listed gender. Use this only when both are on file.
4. **Escalate, need more information** in every other case: no DOB on file, a DOB that falls inside a year-only, circa or range DOB with nothing else that differs, or no listed DOB at all. Say exactly what would resolve it (usually a government ID showing full DOB, or place of birth).

Nationality, place of birth and address differences are recorded in the narrative but never clear a hit on their own.

## The case narrative
For every hit, three to five sentences for the audit file: which list entry hit and on what name, the attributes compared with both values, the call, and the one fact it rests on. Only facts from the evidence. For an escalation, end with what the next reviewer needs to obtain or check.

## What an automated reviewer may do
Propose a call and write the narrative. A clear is posted only when code confirms the deciding fact (rule 2 or 3). Escalations are left open for an analyst. An automated reviewer never fails a customer.
