"""Post the daily streak clip to X with an auto-incrementing day counter.

The day number is derived from START_DATE rather than stored, so a missed run,
a lost state file or a fresh machine can never desync the count.
"""

import os
import sys
import time
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from requests_oauthlib import OAuth1

MEDIA_ENDPOINT = "https://api.x.com/2/media/upload"
TWEET_ENDPOINT = "https://api.x.com/2/tweets"
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB, comfortably under X's 5 MB per-APPEND cap
MAX_PROCESSING_WAIT = 300     # seconds to wait for X to transcode the video

# GitHub's scheduler fires late, so the workflow starts early and this script
# waits out the difference. Two cron entries cover both sides of US daylight
# saving; the one that lands in the wrong half of the year sees a lead time
# past MAX_LEAD and exits quietly, so only one run per day ever posts.
# The workflow is scheduled 26 min early so GitHub's own lateness still leaves
# room to upload and hit the target. MAX_LEAD sits between that 26 min and the
# 86 min the off-season cron would see, so exactly one run per day proceeds.
MAX_LEAD = 45 * 60   # don't wait longer than this - wrong seasonal cron
GRACE = 10 * 60      # started past target anyway - post now rather than skip


class PostError(Exception):
    """Fatal error that should fail the workflow run."""


def load_env():
    """Read .env if present, without clobbering real environment variables."""
    if not os.path.exists(".env"):
        return
    with open(".env", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise PostError(f"Missing required setting: {name}")
    return value


def day_number(start_date, tz):
    """Day 1 is START_DATE itself, counted in the posting timezone."""
    today = datetime.now(ZoneInfo(tz)).date()
    delta = (today - start_date).days + 1
    if delta < 1:
        raise PostError(
            f"START_DATE {start_date} is in the future - today is {today}. "
            "Nothing to post yet."
        )
    return delta, today


def resolve_target(post_at, tz):
    """The exact instant today's post should go out, in the posting timezone."""
    try:
        hh, mm = (int(part) for part in post_at.split(":"))
        target_time = datetime.strptime(f"{hh}:{mm}", "%H:%M").time()
    except (ValueError, TypeError):
        raise PostError(f"POST_AT must be HH:MM in 24h form, got {post_at!r}")

    now = datetime.now(ZoneInfo(tz))
    return now, now.replace(
        hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
    )


def decide(now, target):
    """Should this run post? Returns (bool, reason).

    Two cron entries fire each day, one per daylight-saving offset. This is
    what keeps the off-season one from double-posting.
    """
    lead = (target - now).total_seconds()
    if lead > MAX_LEAD:
        return False, (f"Target is {lead / 60:.0f} min away - this is the "
                       "off-season cron. The other scheduled run handles today.")
    if lead < -GRACE:
        return False, (f"Target passed {-lead / 60:.0f} min ago. Exiting rather "
                       "than posting at the wrong time.")
    return True, f"{lead / 60:.0f} min of lead time"


def wait_until(target, now):
    """Burn the remaining lead time so the post lands on the second."""
    remaining = (target - now).total_seconds()
    if remaining <= 0:
        return
    print(f"Holding {remaining:.0f}s until {target.strftime('%H:%M:%S %Z')}...")
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


def explain(resp, stage):
    """Turn X's terse errors into something actionable at 9am."""
    body = resp.text.lower()
    hint = ""
    if resp.status_code == 401:
        hint = ("\n  -> Keys are wrong, or the Access Token was generated while "
                "the app was still Read-only. Set the app to Read and Write, "
                "then REGENERATE the access token.")
    elif resp.status_code == 403:
        hint = ("\n  -> App lacks write permission, or the account is "
                "restricted. Check app permissions = Read and Write.")
    elif resp.status_code == 429:
        hint = "\n  -> Rate limited. The next scheduled run will retry."
    elif resp.status_code == 402 or "credit" in body or "payment" in body:
        hint = ("\n  -> Out of API credits. Top up in the X developer console; "
                "a daily post costs about $0.015.")
    return f"{stage} failed [HTTP {resp.status_code}]: {resp.text[:500]}{hint}"


def media_id_from(payload):
    """v2 nests the id under 'data'; older shapes return it flat."""
    body = payload.get("data") or payload
    return body.get("id") or body.get("media_id_string")


def upload_video(auth, path, verbose=False):
    """Chunked upload, then poll until X finishes transcoding.

    Uses the v2 sub-path endpoints (/initialize, /{id}/append, /{id}/finalize).
    The older command=INIT/APPEND/FINALIZE form that v1.1 used is rejected
    outright by this endpoint - it reports 'command is not one of []'.
    """
    total_bytes = os.path.getsize(path)

    init = requests.post(
        f"{MEDIA_ENDPOINT}/initialize", auth=auth, timeout=120,
        json={"total_bytes": total_bytes, "media_type": "video/mp4",
              "media_category": "tweet_video"},
    )
    if init.status_code not in (200, 201, 202):
        raise PostError(explain(init, "media initialize"))

    media_id = media_id_from(init.json())
    if not media_id:
        raise PostError(f"initialize returned no media id: {init.text}")
    if verbose:
        print(f"  initialize ok, media_id={media_id}, {total_bytes} bytes")

    with open(path, "rb") as fh:
        index = 0
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            append = requests.post(
                f"{MEDIA_ENDPOINT}/{media_id}/append", auth=auth, timeout=120,
                data={"segment_index": str(index)}, files={"media": chunk},
            )
            if append.status_code not in (200, 201, 204):
                raise PostError(explain(append, f"media append segment {index}"))
            if verbose:
                print(f"  append segment {index} ok ({len(chunk)} bytes)")
            index += 1

    finalize = requests.post(
        f"{MEDIA_ENDPOINT}/{media_id}/finalize", auth=auth, timeout=120)
    if finalize.status_code not in (200, 201):
        raise PostError(explain(finalize, "media finalize"))
    if verbose:
        print("  finalize ok")

    await_processing(auth, media_id, finalize.json(), verbose)
    return media_id


def await_processing(auth, media_id, finalize_body, verbose):
    """Video is transcoded async; posting before it finishes returns a 400."""
    body = finalize_body.get("data") or finalize_body
    info = body.get("processing_info")
    waited = 0

    while info and info.get("state") in ("pending", "in_progress"):
        wait = int(info.get("check_after_secs", 5))
        if waited + wait > MAX_PROCESSING_WAIT:
            raise PostError(
                f"Video still processing after {waited}s - giving up so the run "
                "does not hang. The clip may be too large or malformed."
            )
        if verbose:
            print(f"  processing {info.get('state')}, waiting {wait}s")
        time.sleep(wait)
        waited += wait

        status = requests.get(
            MEDIA_ENDPOINT, auth=auth, timeout=60,
            params={"media_id": str(media_id)},
        )
        if status.status_code != 200:
            raise PostError(explain(status, "media status"))
        info = (status.json().get("data") or status.json()).get("processing_info")

    if info and info.get("state") == "failed":
        err = info.get("error", {})
        raise PostError(
            f"X rejected the video during processing: "
            f"{err.get('name')} - {err.get('message')}"
        )
    if verbose:
        print("  video processed and ready")


def create_post(auth, text, media_id):
    payload = {"text": text, "media": {"media_ids": [str(media_id)]}}
    resp = requests.post(TWEET_ENDPOINT, auth=auth, json=payload, timeout=60)

    if resp.status_code in (200, 201):
        return (resp.json().get("data") or {}).get("id")

    # A byte-identical post is rejected by X. Since the text carries the day
    # number, that can only mean today's post already went out - so a double
    # run is a no-op, not a failure.
    if resp.status_code == 403 and "duplicate" in resp.text.lower():
        print("Already posted today (X rejected it as a duplicate). Nothing to do.")
        sys.exit(0)

    raise PostError(explain(resp, "create post"))


def main():
    parser = argparse.ArgumentParser(description="Post the daily streak clip to X.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything except upload and post.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print each upload step.")
    parser.add_argument("--now", action="store_true",
                        help="Post immediately instead of waiting for POST_AT.")
    args = parser.parse_args()

    load_env()

    tz = os.environ.get("TIMEZONE", "America/New_York").strip()
    post_at = os.environ.get("POST_AT", "11:11").strip()
    media_path = os.environ.get("MEDIA_PATH", "It gets easier final.mp4").strip()
    template = os.environ.get("TEXT_TEMPLATE", "Day {n}").strip()

    try:
        start = date.fromisoformat(require("START_DATE"))
    except ValueError:
        raise PostError("START_DATE must be YYYY-MM-DD, e.g. 2026-09-26")

    n, today = day_number(start, tz)
    text = template.format(n=n)

    if not os.path.exists(media_path):
        raise PostError(f"Clip not found: {media_path}")

    now, target = resolve_target(post_at, tz)

    print(f"Today is {today} ({tz}) -> Day {n}")
    print(f"Text: {text!r}")
    print(f"Clip: {media_path} ({os.path.getsize(media_path) / 1048576:.2f} MB)")
    print(f"Now {now.strftime('%H:%M:%S %Z')}, target {target.strftime('%H:%M:%S %Z')}")

    if not args.now:
        proceed, reason = decide(now, target)
        print(reason)
        if not proceed:
            return

    if args.dry_run:
        print("\nDRY RUN - nothing uploaded, nothing posted, no credits spent.")
        return

    auth = OAuth1(
        require("X_API_KEY"), require("X_API_SECRET"),
        require("X_ACCESS_TOKEN"), require("X_ACCESS_TOKEN_SECRET"),
    )

    # Upload before the deadline: transcoding takes a variable few seconds and
    # would otherwise push the post past 11:11.
    print("Uploading clip...")
    media_id = upload_video(auth, media_path, args.verbose)

    if not args.now:
        wait_until(target, datetime.now(ZoneInfo(tz)))

    print("Posting...")
    tweet_id = create_post(auth, text, media_id)
    posted_at = datetime.now(ZoneInfo(tz)).strftime("%H:%M:%S %Z")
    print(f"Posted Day {n} at {posted_at}: https://x.com/i/status/{tweet_id}")


if __name__ == "__main__":
    try:
        main()
    except PostError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"\nNETWORK ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
