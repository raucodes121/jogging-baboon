# Daily X streak poster

Posts a fixed clip to [@everydayitgets](https://x.com/everydayitgets) every day
at 11:11:00 America/New_York, with an auto-incrementing day counter.

The day number is computed as `(today - START_DATE) + 1`, not stored. A missed
run, a wiped machine or a fresh checkout cannot desync the count.

## Architecture, and why it looks like this

```
cron-job.org  --POST-->  GitHub workflow_dispatch  -->  post.py  -->  X
 (the timer)              (the runner)                  (the work)

GitHub cron ------------> same workflow, --catch-up -->  safety net
```

**The daily trigger is external, on purpose.** GitHub Actions' own `schedule`
was the original trigger and it failed outright:

| Date | Due | Actually ran | Late by |
|---|---|---|---|
| 2026-09-25 | 15:45 UTC | 18:52 UTC | 187 min |
| 2026-09-25 | 15:45 UTC | 19:32 UTC | 227 min |
| 2026-09-26 | 14:45 UTC | never | — |

GitHub documents `schedule` as best-effort and delayed under load. In practice
that meant hours, then a silent no-show. Dispatched runs, by contrast, started
within ~30s every single time — the runner is fine, only the timer was broken.
So the timer moved out and everything else stayed.

## How 11:11 stays sharp

1. cron-job.org fires at **10:45 America/New_York** (it is DST-aware, so there
   is no UTC maths and no seasonal duplicate entry).
2. It POSTs to GitHub's `workflow_dispatch` API; the run starts in ~30s.
3. `post.py` **uploads the video first** — transcoding is slow and variable, so
   it must finish before the deadline, not after.
4. It then sleeps until the wall clock hits 11:11:00 and fires the post.
   Measured drift on the wait is ~1ms.

That leaves ~26 minutes of slack to absorb any delay along the chain. If a run
starts *after* 11:11 it posts anyway when within 10 minutes; beyond that it
**exits non-zero** so GitHub emails you, rather than reporting a green
checkmark for a day that never posted.

## The safety net

One `schedule` remains, at 20:00 UTC — hours after the target, where GitHub's
unreliability stops mattering. It runs `--catch-up`, which reads the timeline
and posts **only if the day's post never went out**.

It matches the day text exactly rather than relying on X rejecting duplicates,
so `Day 3` can never match `Day 30`, and it cannot double-post. The account id
is parsed from the OAuth token's `<id>-<secret>` form, so the check costs one
$0.005 timeline read and no billed user lookup.

Net effect: the streak survives even if cron-job.org dies, just later in the day.

## Setup

### 1. X app

In the [developer portal](https://developer.x.com), app `rozpost`:

1. **User authentication settings** → App permissions → **Read and Write** → Save.
2. **Keys and tokens** → Access Token and Secret → **Generate**.
   In that order. A token minted while the app was Read-only stays read-only
   forever and fails with a 401 that looks like bad keys.
3. Buy credits. A post with media costs ~$0.015, so $10 is roughly two years.

### 2. Local

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in the four keys and START_DATE
python post.py --check        # verifies credentials, posts nothing
python post.py --dry-run --now
python post.py --verbose --now  # real post
```

### 3. GitHub

**Settings → Secrets and variables → Actions → Secrets:**
`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`

**Variables tab:** `START_DATE` = date of Day 1, `YYYY-MM-DD`

### 4. The external timer

A fine-grained PAT scoped to this repo only, with **Actions: Read and write**,
drives a cron-job.org job:

```
URL     POST https://api.github.com/repos/raucodes121/jogging-baboon/actions/workflows/daily-post.yml/dispatches
When    10:45 daily, timezone America/New_York
Headers Accept: application/vnd.github+json
        Authorization: Bearer <PAT>
        X-GitHub-Api-Version: 2022-11-28
Body    {"ref":"main"}
```

A successful dispatch returns **204 No Content**.

## Flags

| Flag | Effect |
|---|---|
| `--check` | Verify credentials, print the handle, post nothing |
| `--dry-run` | Resolve day number and clip, post nothing, spend nothing |
| `--now` | Skip the wait, post immediately |
| `--catch-up` | Post only if today's post never went out |
| `--verbose` | Print each upload step |

## Known future failure modes

- **The PAT expires.** Whatever expiry you chose, the timer dies silently that
  day. The `--catch-up` net still covers it, so you lose the 11:11 timing but
  not the streak. Diarise it.
- **Credits run out.** Posting fails with HTTP 402; the script says so in plain
  English and the run fails, so you get an email.
- **GitHub disables the schedule after 60 days** of repo inactivity, which would
  cost you the safety net (not the primary trigger). Any commit resets it.

## Notes

- **Don't add links** to the text. A post containing a URL costs $0.20 instead
  of $0.015.
- **Clip limits:** 2:20 max without X Premium. Current clip is 19s, H.264 + AAC,
  1.3 MB.
- **`START_DATE` lives in GitHub**, not the code. Changing it shifts every
  future day number.
