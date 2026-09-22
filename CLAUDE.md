# NASLUX

Audio-to-chords/tab recognition service (naslux.ru) with paid subscriptions
(GetPlatinum v2 payment integration). Production server: root@62.113.97.65,
code at `/opt/nasluh/app`, service `nasluh.service`. Deploy details, daily
ops commands and architecture decisions: `deploy/README.md`.

Autodeploy: pushes to this branch reach the server via a GitHub webhook
(`deploy/install_deploy_webhook.sh`, listener is `nasluh-deploy-webhook.service`,
separate process from the main app on purpose — see its docstring). A push
here becomes a live deploy within about a minute, without needing SSH.

## Standing mandate (2026-09-22)

The project owner wants ~500k RUB/month revenue from this site and says
chord-recognition quality is the main blocker; they've granted broad
autonomous authority to improve it — code changes, dependency additions,
competitor research, bug fixes — without asking for confirmation first.

What that does NOT cover, use judgment on regardless of the mandate:
- Real recurring money spend (e.g. renting a GPU server) — state the cost
  plainly rather than silently provisioning paid infrastructure. A cloud
  session has no payment credentials to do this anyway.
- Deceptive/gray-hat competitor tactics (scraping behind auth, fake
  reviews) — "use all available means" means don't hold back on legitimate
  technical/product work, not license for anything unethical.
- Destructive/irreversible changes to real user or payment data without a
  backup/rollback path.
- SSH access to the production server needs a password only available
  interactively in a local session — a cloud/automated run should stick to
  code-level work (this repo) and let the webhook handle deployment, not
  attempt to SSH in.

Known context already gathered: global competitors exist and are mature
(Moises.ai, Chord AI, Yamaha Chord Tracker) — the real edge for a Russian
audience is likely being payable in RUB / accessible when foreign apps'
payment methods aren't, not necessarily raw chord-detection accuracy alone.
Worth validating further rather than assuming.

There's an in-progress hypothesis in `scripts/compare_separation.py`:
running vocal separation (BS-RoFormer) before Demucs guitar separation may
give cleaner guitar stems than Demucs alone. Confirmed impractical on the
production server's CPU (2+ hours for one track) — would need a GPU to be
viable. Judging whether the audio actually sounds better needs a human ear,
not something a text-only agent can verify itself.
