# 0047. The reply language and the stored language stay in step; one word never switches

- Status: Accepted, amended by 0051
- Date: 2026-10-10
- Refines: 0042 (the `language` field on `get_application`) and Phase 1 section 4.3

## Context
In a real Haiku 5.5 run, a Candidate wrote everything in English and answered the start date with the single word "Mañana". The stored language stayed `en` and the summary was English, but the closing message after `finish_screening` was Spanish. Cause: `finish_screening` returned only `{stage: qualified}`, and the Screener skill held one Qualified close example, in Spanish (es-MX), which the model copied. No code picked a Spanish template; the text came from the skill. The same run showed the open rule "reply in the language of the latest message" reading one foreign word as a switch.

## Decision
- **One word does not switch.** A one-word or very short answer in another language ("Mañana", "Sí", "OK", a place name, a number) is not a switch. The agent switches only on a clear signal: a full sentence in the other language, a request to switch, or a greeting that opens the chat. This is wording in the Screener skill; the model decides (0042), and code does not detect language from word lists.
- **Reply and record stay in step.** The agent replies in the `language` that `get_application` returns, never another. To reply in a different language it passes that language on the same `get_application` call, so the record and the reply cannot disagree. The tool result already carries the stored language every turn.
- **Code-built text follows the stored language.** `finish_screening` returns `message`, the Qualified close in the Application's stored language (es-ES, es-MX or en; `QUALIFIED_CLOSE` in `criba.channel.copy`), and the agent sends it exactly as given, as it does for a Knockout. The skill no longer carries a close example to copy. Nudges, the opt-out reply, Knockouts, the Interview offer and the fallback text already looked up the stored language; tests now cover all three variants.

## Alternatives considered
- **Skill wording only:** the one-word rule is wording, but the closing text would still depend on the model not copying a Spanish example. A guaranteed close removes the failure instead of asking the model to avoid it.
- **Detect the language in the router with word lists:** rejected by 0042 (it misfires on place names and short answers, and this is the same class of bug).
- **Send the close from the router after `finish_screening`:** a second message in the same turn when the agent also writes one, and a new path around the agent's reply. The Knockout pattern (agent relays the tool's text) already exists.

## Consequences
- **Contract:** `finish_screening` output gains a required `message` (own commit, vendored copy updated).
- **Tokens:** the skill is about the same size (the es-MX close example is gone, the language rule is two sentences, and the `get_application` schema carries the same rule in one line). The close, about 90 characters, now sits in the history once; its text was kept short for that reason.
- **Simulator:** the stub replies in the language `get_application` returned and sends no `language` for a one-word answer; persona `language_one_word_en` checks that an English Candidate who answers "Mañana" stays `en` end to end (`reply_language: en`). `language_switch_mx` and `opted_out_code_switch_es` still cover real switches.
