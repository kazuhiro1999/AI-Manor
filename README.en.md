*日本語版はこちら → [README.md](README.md)*

# AI Manor

**An AI butler for your household, and the staff who work for them.** A personal assistant that runs on
[Claude Code](https://claude.com/claude-code) and stays entirely on your own machine.

Talk to the butler and they take the task, work out the order of things, and hand it to the right member of
staff. The staff cover the kitchen, the chores, the money, and the calendar: they plan meals, keep the
shopping list, watch what's running low, record what you spend, and lay out your day. Everything is kept in a
database on your machine and shown in a local web app. **Nothing is sent anywhere.**

![Dashboard](docs/screenshots/dashboard.png)

*Every screenshot is from `manor init --demo` — a **fictional household**.*

**Contents** — [What you can ask](#things-you-can-ask-for) · [Quick start](#quick-start) ·
[Screens](#screens) · [Who handles what](#who-handles-what) · [Also included](#also-included) ·
[Your data](#your-data) · [Docs](#learn-more) · [License](#license)

## Things you can ask for

| You say | What happens |
|---|---|
| "Add milk and eggs to the shopping list" | It's on the list, waiting the next time you open the kitchen |
| "What can I make with what's in the fridge?" | A meal suggested from what you have, your tastes and allergies — logged once you cook it |
| "Dentist next Wednesday" | It's on the calendar, in tomorrow's agenda, and in the morning nudge |
| "How much did I spend this month?" | A breakdown by category, against your budget |
| "Where did we get to on this?" | Just the decisions, notes and related tasks that bear on it — not everything |
| "We're nearly out of detergent" | Stock level drops, and it moves onto the shopping list before it runs out |

Anything with consequences outside your machine — sending, publishing, deleting, spending — is **not done on
its own**. It's queued for your approval.

## Quick start

**You'll need**: [uv](https://docs.astral.sh/uv/), [Claude Code](https://claude.com/claude-code), and Node.js.
Tested on Windows and macOS; there are no OS-specific scripts, so Linux should work too.

```bash
uv sync                        # install dependencies
uv run manor init --demo       # database plus synthetic data (a fictional household)
uv run manor web build         # build the web app (first time only)
uv run manor web serve --open  # http://127.0.0.1:8789/
git config core.hooksPath .githooks   # if you put it under git (the leak-stopping hook)
```

Then open this folder in **Claude Code** — from that point on it acts as your butler. (Accept the initial
"trust this workspace" prompt; without it the permission allowlist and hooks won't take effect.)

For your own household, drop `--demo`. Opening the app with nothing in it starts **first-run setup**, which
asks what to call you, which features you want, and your first project and task — every step can be skipped.

Every command has the shape `manor <group> <verb> [...] [--json]`. For exact arguments, `manor <group> --help`.

## Screens

![Kitchen](docs/screenshots/kitchen.png)

Pantry by expiry, shopping list by aisle, meals kept as a log. More screens in
[`docs/screenshots/`](docs/screenshots/); architecture diagrams (with an interactive version) in
[`docs/diagrams/`](docs/diagrams/).

## Who handles what

| Who | Ask them for (examples) | CLI group |
|---|---|---|
| Butler | Tasks & projects, ruling on pending approvals, delegating to staff, consistency checks, assembling context | `manor task` `project` `decision` `handoff` `check` `ctx` |
| Chef | Pantry, meal suggestions & logging, shopping lists, tastes and allergies | `manor chef` |
| Housekeeper | Chore rotation, supply levels, maintenance cycles, bin day | `manor house` |
| Steward | Spending & income, recurring due dates, budget variance, monthly trends | `manor money` |
| Secretary | Calendar, daily agenda, inbox triage, resolving relative dates | `manor sec` |
| QA | Reviews what gets built. Doesn't fix it | `manor talk qa` |
| Auditor | Reviews the butler's own rules from the outside, once a month | `manor talk auditor` |

**There is no do-everything agent.** A role exists only for a domain that comes up repeatedly and is complete
in itself, and each member of staff can only write to their own tables. See [`docs/staff/`](docs/staff/) for
what each can be asked; `manor talk <name>` talks to one directly, without the butler.

## Also included

| Feature | What it does | More |
|---|---|---|
| **Avatar window** `manor face` | A VRM avatar in the corner of the screen; talk to it to make requests. Each member of staff can have their own face and voice (a default avatar is bundled) | [`docs/face.md`](docs/face.md) |
| **Spoken nudges** `manor notify` | Speaks up **only when pending approvals increase**, once, and never at night | [`docs/notify.md`](docs/notify.md) |
| **From your phone** Tailscale | `tailscale serve` plus a passcode. No public URL is created | [`docs/tailscale.md`](docs/tailscale.md) |
| **Calendar** `manor calendar` | Reads an ICS feed into your agenda (read-only; nothing is written back) | [ADR-012](docs/design/ADR-012_calendar_and_i18n.md) |
| **Night shift** `manor night` | Runs only what you wrote in the night-shift brief while you sleep (OS scheduling off by default) | [`docs/night.md`](docs/night.md) |
| **Desktop shortcut** `manor shortcut create` | Stops any running server, rebuilds, starts, opens the browser | [`docs/shortcut.md`](docs/shortcut.md) |
| **House rules** `manor rule` | Curfews, how to handle visitors, and so on, with scope and tags | [`docs/rules.md`](docs/rules.md) |
| **English / Japanese** | Both the app and the CLI (Settings → Language) | [ADR-012](docs/design/ADR-012_calendar_and_i18n.md) |

Voice (VOICEVOX), Slack and Notion are optional extensions. **Install none and it still works completely**
([`docs/web.md`](docs/web.md), [`docs/voice.md`](docs/voice.md)).

## Your data

- **Nothing leaves your machine.** It reads and writes a local SQLite database, and anything outbound waits
  for your approval
- **Your data is not in git.** `home/` — the database, what to call you, tasks, calendar, finances — is
  untracked by default
- **Leaks are stopped before they're committed.** Put names and other sensitive terms in
  `~/.manor/git-leak-terms.txt` (**outside the repository**) and the pre-commit hook refuses such a commit

## Learn more

| What you want | Where |
|---|---|
| What each staff agent can be asked to do | [`docs/staff/`](docs/staff/) |
| The design decisions themselves (ADRs) | [`docs/design/`](docs/design/) |
| Full documentation index | [`docs/README.md`](docs/README.md) |
| Current direction (the butler's own working notes) | [`ROADMAP.md`](ROADMAP.md) (Japanese) |
| History of behavioural changes | [`CHANGELOG.md`](CHANGELOG.md) (Japanese) |

## License

**MIT License** ([`LICENSE`](LICENSE)).

**The bundled avatar `assets/face/default.vrm` is not covered by it.** It was created in
[VRoid Studio](https://vroid.com/studio) from pixiv Inc.'s official **AvatarSample** model and follows
[VRoid's sample-model terms](https://vroid.pixiv.help/hc/ja/articles/4402394424089-AvatarSample-A-Z):
free redistribution, modification and commercial use are allowed, but **redistribution for a fee is not**
([`assets/face/NOTICE.md`](assets/face/NOTICE.md)). To use your own avatar you don't need to replace the
bundled file — upload one from Settings → Avatar and yours takes precedence.
