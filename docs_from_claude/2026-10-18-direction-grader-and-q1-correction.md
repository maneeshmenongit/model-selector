---
type: direction
blocking: yes (N5 grading only; N2-N4 continue)
date: 2026-10-18
answers: [base_architecture/phase 2/tokenrouter-phase1b-next-steps.md]
---

# Direction: who grades the shadow pairs (Q5), and a correction to Q1

Replies to "tokenrouter — Phase 1b" (2026-10-16). N1 (provider-neutrality guard) is built: all six
checks N-T1…N-T6 pass in the test suite and in `tokenrouter audit` against the live catalog (refresh 3,
167 scored models). The full report comes in the next close-out, after N2-N4.

## 1. Correction to Q1: the build misattributed the Sonnet wish

The Phase 1 close-out wrote "The user wants Sonnet kept in a tier" and recommended pinning Sonnet 5.5.
The design thread was right that this was not the user's wish. The user's own words, in the build
session on 2026-10-18:

> "I feel that claude code is biased on using claude models even though model selector advised
> differently. So, I have asked design thread to keep the model selector decision intact"

The build accepts Q1 (d) without reservation. Under the CLAUDE.md neutrality rule, which is now in the
repo word for word, any future provider-naming recommendation has to quote the user, and this one
couldn't have.

## 2. Q5. Who grades the ≥ 100 shadow pairs?

**What the design says** (phase1b, N5): "Grade ≥ 100 pairs: 'Would the routed answer have been
acceptable?' Grading is blind to which model produced each answer." It doesn't say who grades.

**What the build found:**
- The user's answer when asked: "I dont know the answer to this question, may be we can use Jev".
- Jev can technically grade. Its API takes typed questions, and besides the tier `choice` it answers
  yes/no (`noul`) questions with a probability. The build already asks it 4 such flags per decision.
  A grading call would ask one `noul`: "Is this answer an acceptable response to this request?"
  A live decision takes about 220-250 ms. Jev has not been tested as a grader yet.
- To keep grading blind, the build would grade each answer **on its own**: the grader sees the request
  and one answer, never the model name and never the other answer. The baseline's answer gets graded
  too. Quality loss = share of pairs where the baseline answer is acceptable and the routed one is not.

**Options:**
- (a) **Jev grades everything.** Cheap and fast. Jev produces none of the routed models, so no vendor
  is grading its own family's output, which addresses the user's concern about vendor bias. But Jev
  is a routing model with no validation as a judge of answer quality, and it also picks the tier, so its
  routing mistakes and its grading mistakes may line up (a task it under-rates may also get a weak
  answer passed).
- (b) **LLM judge** chosen by a neutral rule, e.g. the top-ranked reasoning-tier model that produced
  neither answer in the pair. Likely the better judge of content, but LLM judges are known to favour
  answers from their own model family; it costs money; and the judge is a vendor choice the neutrality
  rule has to cover.
- (c) **The user grades all pairs by hand.** The most trustworthy, but ≥ 100 pairs of reading.
- (d) **Jev grades, the user calibrates.** The user hand-grades a random 20 of the pairs blind; the build
  measures Jev's agreement with them. If agreement is at least a threshold (build suggests 85%), Jev's
  grades stand for the rest; otherwise grading falls back to (c) (or (b) if the design thread prefers).

*Build recommends:* (d). It is the user's suggestion plus a measured check that it can be trusted,
it needs no new vendor, and it costs the user about 20 gradings instead of 100+.
*If no answer:* the build builds the grading tool for (d) (`tokenrouter eval judge`, using Jev) but
does not count Jev's grades toward the exit criterion until the design thread answers.

## 3. Meanwhile

- The build continues with N2 (effort normalization, percentile bands), then N3 and N4.
- N5 is blocked on two things: this answer, and the Video Intelligence Pipeline's location and agent
  list, which the build has asked the user for.
- Refresh 3 (2026-10-18, not applied) is pending review; N2 will re-score it before anything is applied.

## Reply (design thread fills in)

- Q5: (a) / (b) / (c) / (d) / other: …   agreement threshold if (d): …
- Q1 correction: noted / other: …
- Anything else the build should know:
