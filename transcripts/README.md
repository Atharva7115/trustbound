# AI Transcripts

This folder documents AI tool usage during the Bhume Boundary Correction
take-home assignment, as required by the submission contract.

> *"Use any AI tools you like. We expect it. We're assessing how you direct them,
> not whether you typed every line."* — Bhume assignment brief

---

## Tools Used

| Tool | Role |
|---|---|
| **ChatGPT** (web) | Problem framing, domain research, strategy design, debugging reasoning, evaluation review, README preparation |
| **Kiro** (IDE agent) | Implementation, experimentation, refactoring, diagnostic generation, report writing |

---

## Web-Chat Conversations

| Session | Link |
|---|---|
| ChatGPT — Full project discussion | https://chatgpt.com/share/6a2fb703-72b8-83ee-b12b-94eb5f9bfd58 |

---

## Local Transcript Files

| File | Contents |
|---|---|
| `chatgpt_summary.md` | Summary of ChatGPT discussions covering problem understanding, strategy design, and key technical decisions |
| `kiro_summary.md` | Phase-by-phase record of the Kiro implementation session — what was built, findings, bugs fixed, and outcomes |

---

## How AI Was Directed

AI was used in two distinct modes throughout this project:

**ChatGPT (reasoning and strategy)**
Used in web chat to understand the problem domain before writing any code.
Discussions covered cadastral drift, Maharashtra land record structure (7/12 / satbara),
why confidence calibration is weighted most in the grading rubric, and how to design
a pipeline that generalises without village-specific tuning. Key strategy decisions
(perimeter band scoring, S3 as highest-weight signal, threshold=0.50 by simulation)
were discussed here before implementation.

**Kiro (implementation under direction)**
Used in the IDE to write, test, and iterate on each phase. Every implementation
request was preceded by a measurement or probe. No signal was added without first
verifying it correlated with the right outcome on truth data. No threshold was set
without simulating its effect. Kiro handled the typing; engineering judgment
directed the decisions.

---

## Transparency Statement

Every design decision in this codebase was preceded by a measurement.
The judgment — which signals to trust, what confidence means, when to flag —
was made by the engineer. The AI tools executed those decisions.
