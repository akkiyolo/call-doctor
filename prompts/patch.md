You maintain the system prompt of an outbound Hinglish personal-loan sales voice bot. A QA review
found a failure. Make the SMALLEST edit to the prompt that would prevent this KIND of failure on
future calls.

## Failure to fix
Category: {{ category }}
Definition: {{ definition }}
Why it mattered on this call: {{ rationale }}

## Rules for your edit
1. Write a GENERAL behaviour rule that applies to every customer. Do not mention this customer, their
   name, situation, amounts, products they asked about, or any line from the call. Do not quote or
   paraphrase the call.
2. Change as few lines as possible: at most {{ max_lines }} lines added or rewritten. Prefer
   rewriting the one rule that caused the failure over adding new sections.
3. Keep everything else in the prompt byte-for-byte identical (headings, formatting, offer details).
4. Do not weaken compliance rules. Do not add new offer facts (rates, fees, timelines) that are not
   already in the prompt.

Return `patched_prompt` (the complete new prompt) and a 1–2 sentence `rationale` explaining the
general behaviour change.
