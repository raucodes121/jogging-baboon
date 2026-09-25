# Daily X streak poster

Posts the same clip to X every day with an auto-incrementing day counter.

The day number is computed as `(today - START_DATE) + 1`, not stored anywhere.
A missed run, a wiped machine or a re-cloned repo can't desync the count.

## Setup

### 1. Finish the X app setup

In the [developer portal](https://developer.x.com), open the `rozpost` app:

1. **User authentication settings** → App permissions → **Read and Write** → Save.
2. **Keys and tokens** → Access Token and Secret → **Generate**.
   Do this *after* step 1. A token minted while the app was Read-only stays
   read-only forever and fails with a 401 that looks like bad keys.
3. Buy the **$10 credit pack**. A post with media costs ~$0.015, so that's
   roughly two years of daily posting.

### 2. Local test

```bash
pip install -r requirements.txt
cp .env.example .env     # fill in the four keys and START_DATE
python post.py --dry-run --now  # prints day number and text, posts nothing
```

Verify the credentials resolve to the right account without posting:

```bash
python post.py --check
```

When that looks right, a real post to prove the pipe end to end:

```bash
python post.py --verbose --now
```

`--check` also exists as a checkbox on the manual **Run workflow** trigger, which
is the fastest way to confirm the GitHub secrets are correct after changing them.

### 3. GitHub Actions

Create a **private** repo and push. Then in repo **Settings**:

- **Secrets and variables → Actions → Secrets**, add four:
  `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`
- **Variables** tab, add one: `START_DATE` = the date of Day 1, `YYYY-MM-DD`

Trigger it by hand from the **Actions** tab (`Run workflow`) to test — there's a
dry-run checkbox.

## How 11:11 stays sharp

GitHub's scheduler is best-effort and routinely fires 5–15 minutes late, so a
naive `cron: 11 11 * * *` would post at 11:17, 11:23, 11:14. Instead:

1. Two cron entries fire **26 minutes early**, one per daylight-saving offset
   (14:45 UTC = 10:45 EDT, 15:45 UTC = 10:45 EST).
2. Whichever one is in the wrong half of the year sees a lead time past
   `MAX_LEAD` and exits without posting. Exactly one run per day proceeds —
   verified across both DST changeover days with lag up to 35 minutes.
3. The surviving run **uploads the video first**, so transcoding (slow and
   variable) finishes before the deadline.
4. It then sleeps until the wall clock hits 11:11:00 and fires the post.
   Measured drift on the wait is ~1ms; network latency to X adds well under a
   second.

If a run somehow starts *after* 11:11, it posts immediately if within 10
minutes, and skips rather than posting at a visibly wrong time beyond that.

To move the time, change `POST_AT` in the workflow **and** shift both `cron`
lines by the same amount.

## Notes

- **Failure alerts** come free: GitHub emails you when a scheduled run fails.
  Check Actions → the run → the failed step for the exact error. The off-season
  cron exits 0, so it never generates a false alarm.
- **Double runs are safe.** X rejects a byte-identical post, and since the text
  carries the day number, that rejection means today's post already went out —
  the script treats it as success and exits 0.
- **Make the repo public** if you want unlimited Actions minutes. The waiting
  step burns ~26 min/day, which is ~780 min/month against the 2,000-minute free
  allowance on private repos. It fits, but public removes the ceiling. Your
  secrets stay encrypted either way.
- **Don't add links** to the text. A post containing a URL costs $0.20 instead
  of $0.015.
- **Clip limits:** 2:20 max without X Premium. The current clip is 19s, H.264 +
  AAC, 1.3 MB.

## Files

| File | Purpose |
|---|---|
| [post.py](post.py) | The whole thing: day math, chunked upload, post |
| [.github/workflows/daily-post.yml](.github/workflows/daily-post.yml) | Daily schedule |
| [.env.example](.env.example) | Template for local config |

`twt.py` and `tempCodeRunnerFile.py` are the 2024 attempts. They use API v1.1
endpoints that no longer exist and have credentials hardcoded. They're
gitignored. Delete them once the new setup works.
