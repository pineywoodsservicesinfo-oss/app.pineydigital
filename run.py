#!/usr/bin/env python3
"""
run.py — Piney Digital Outreach System
Main entry point. Run individual modules from the command line.

Usage:
  python run.py scrape            # Module 1 — scrape Google Maps leads
  python run.py check             # Module 2 — check + score each website
  python run.py enrich            # Module 3 — find owner name + email
  python run.py enrich 95         # Module 3 — only score >= 95 leads
  python run.py enrich 60 50      # Module 3 — score >= 60, max 50 leads
  python run.py write --dry       # Module 4 — preview AI messages (free, nothing saved)
  python run.py write             # Module 4 — write + queue all messages
  python run.py write 95          # Module 4 — only score >= 95 leads
  python run.py write 60 20       # Module 4 — score >= 60, max 20 leads
  python run.py hot               # Show top 50 hot leads by score
  python run.py enriched          # Show leads with contact info found
  python run.py queued            # Show leads with messages written + queued
  python run.py stats             # Print DB summary
  python run.py leads             # Print full leads table
  python run.py init              # Initialise / reset database
  python run.py replies           # Show all inbound replies + intent
  python run.py reply-server      # Start Module 7 webhook server (port 5001)
  python run.py send              # Module 5 — send queued SMS (checks time window)
  python run.py send 10           # Module 5 — send max 10 messages
  python run.py send --dry        # Module 5 — dry run, sends nothing
  python run.py send --force      # Module 5 — ignore time window (testing only)
  python run.py call              # Module 8 — AI voice calls via Vapi
  python run.py call --dry        # Module 8 — preview calls (no actual calls)
  python run.py call 10           # Module 8 — call max 10 leads
  python run.py call 10 80        # Module 8 — call max 10 leads with score >= 80
  python run.py call --force      # Module 8 — ignore time window (testing only)
  python run.py calls             # Show recent call history
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.database import init_db, db_stats, get_leads, get_connection


def print_stats():
    init_db()
    stats = db_stats()
    total = stats.pop("total", 0)
    print("\n── Piney Digital Lead Database ──────────────────")
    print(f"  Total leads:  {total}")
    for status, count in sorted(stats.items()):
        print(f"  {status:<20} {count}")
    print("─────────────────────────────────────────────────\n")


def print_leads(limit=30):
    init_db()
    leads = get_leads(limit=limit)
    if not leads:
        print("\n  No leads yet. Run: python run.py scrape\n")
        return
    print(f"\n── Latest {len(leads)} leads ──────────────────────────────")
    print(f"  {'#':<4} {'Business':<35} {'City':<14} {'Category':<22} {'Phone':<16} {'Website'}")
    print("  " + "─"*110)
    for i, l in enumerate(leads, 1):
        print(f"  {i:<4} {(l['business_name'] or '')[:34]:<35} "
              f"{(l['city'] or '')[:13]:<14} {(l['category'] or '')[:21]:<22} "
              f"{(l['phone'] or '')[:15]:<16} {(l['website'] or '')[:30]}")
    print()


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    # ── Module 1: Scrape ──────────────────────────────────
    if command == "scrape":
        print("\n Starting lead scraper…\n")
        from modules.scraper import run_scraper
        new_leads = run_scraper()
        print(f"\n Done. {new_leads} new leads added.\n")
        print_stats()

    # ── Module 2: Website checker ─────────────────────────
    elif command == "check":
        print("\n Starting website checker…\n")
        from modules.website_checker import run_website_checker
        counts = run_website_checker()
        hot = counts["none"] + counts["parked"]
        print(f"\n Done. {hot} hot leads ready for outreach.\n")
        print_stats()

    # ── Module 3: Enrichment ──────────────────────────────
    elif command == "enrich":
        min_score = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        limit     = int(sys.argv[3]) if len(sys.argv) > 3 else None
        print(f"\n Starting contact enrichment (score >= {min_score})…\n")
        from modules.enrichment import run_enrichment
        result = run_enrichment(min_score=min_score, limit=limit)
        print(f"\n Done. Emails: {result['emails']}/{result['total']}  "
              f"Names: {result['names']}/{result['total']}\n")
        print_stats()

    # ── Module 4: AI Writer ───────────────────────────────
    elif command == "write":
        dry_run   = "--dry" in sys.argv
        args      = [a for a in sys.argv[2:] if not a.startswith("--")]
        min_score = int(args[0]) if len(args) > 0 else 60
        limit     = int(args[1]) if len(args) > 1 else None
        mode_lbl  = "DRY RUN preview" if dry_run else "LIVE — writing + queuing"
        print(f"\n Writing messages ({mode_lbl}, score >= {min_score})…\n")
        from modules.writer import run_writer
        result = run_writer(min_score=min_score, limit=limit, dry_run=dry_run)
        if "error" in result:
            print(f"\n Error: {result['error']}\n")
        else:
            print(f"\n Done. Written: {result['written']}/{result['total']}  "
                  f"Failed: {result['failed']}\n")
            if not dry_run:
                print("  Messages queued. Run: python run.py send\n")
            print_stats()

    # ── View: hot leads ───────────────────────────────────
    elif command == "hot":
        init_db()
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT business_name, city, site_status, lead_score, phone
            FROM leads
            WHERE site_status IN ('none','parked','outdated')
            ORDER BY lead_score DESC LIMIT 50
        """)
        rows = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
        conn.close()
        print(f"\n── Top {len(rows)} hot leads ─────────────────────────────────")
        print(f"  {'#':<4} {'Business':<32} {'City':<14} {'Status':<10} {'Score':<7} {'Phone'}")
        print("  " + "─"*85)
        for i, r in enumerate(rows, 1):
            print(f"  {i:<4} {(r['business_name'] or '')[:31]:<32} "
                  f"{(r['city'] or '')[:13]:<14} {(r['site_status'] or ''):<10} "
                  f"{(r['lead_score'] or 0):<7} {r['phone'] or 'no phone'}")
        print()

    # ── View: enriched leads ──────────────────────────────
    elif command == "enriched":
        init_db()
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT business_name, city, site_status, lead_score,
                   owner_name, owner_email, phone, email_source
            FROM leads
            WHERE owner_email IS NOT NULL AND owner_email != ''
            ORDER BY lead_score DESC LIMIT 50
        """)
        rows = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
        conn.close()
        if not rows:
            print("\n  No enriched leads yet. Run: python run.py enrich\n")
            return
        print(f"\n── {len(rows)} enriched leads ──────────────────────────────────")
        print(f"  {'#':<4} {'Business':<28} {'Owner':<20} {'Email':<32} {'Source'}")
        print("  " + "─"*95)
        for i, r in enumerate(rows, 1):
            print(f"  {i:<4} {(r['business_name'] or '')[:27]:<28} "
                  f"{(r['owner_name'] or '—')[:19]:<20} "
                  f"{(r['owner_email'] or '')[:31]:<32} "
                  f"{r['email_source'] or ''}")
        print()

    # ── View: queued messages ─────────────────────────────
    elif command == "queued":
        init_db()
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT business_name, city, phone, lead_score, notes
            FROM leads
            WHERE outreach_status = 'queued'
            ORDER BY lead_score DESC LIMIT 30
        """)
        rows = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
        conn.close()
        if not rows:
            print("\n  No queued messages yet. Run: python run.py write\n")
            return
        print(f"\n── {len(rows)} leads with messages queued ──────────────────────")
        for i, r in enumerate(rows, 1):
            try:
                msg = json.loads(r["notes"] or "{}")
                sms = msg.get("sms", "")
            except Exception:
                sms = ""
            print(f"\n  {i}. {r['business_name']} ({r['city']}) — score: {r['lead_score']}")
            print(f"     Phone: {r['phone']}")
            print(f"     SMS  : {sms}")
        print()


    # ── Module 5: Sender ──────────────────────────────────
    elif command == "send":
        dry_run   = "--dry"   in sys.argv
        force     = "--force" in sys.argv
        args      = [a for a in sys.argv[2:] if not a.startswith("--")]
        limit     = int(args[0]) if len(args) > 0 else None
        mode_lbl  = "DRY RUN" if dry_run else "LIVE — sending for real"
        print(f"\n Sending SMS ({mode_lbl})…\n")
        from modules.sender import run_sender, is_sending_window, get_central_time_str
        print(f"  Current time : {get_central_time_str()}")
        allowed, reason = is_sending_window()
        print(f"  Window check : {reason}\n")
        result = run_sender(limit=limit, dry_run=dry_run, force=force)
        print(f"\n Done.")
        print(f"  Sent    : {result['sent']}")
        print(f"  Failed  : {result['failed']}")
        print(f"  Skipped : {result['skipped']}")
        if result.get('reason'):
            print(f"  Reason  : {result['reason']}")
        print()
        print_stats()

    # ── Module 8: AI Voice Calling ───────────────────────
    elif command == "call":
        dry_run   = "--dry"   in sys.argv
        force     = "--force" in sys.argv
        args      = [a for a in sys.argv[2:] if not a.startswith("--")]
        limit     = int(args[0]) if len(args) > 0 else None
        min_score = int(args[1]) if len(args) > 1 else 60
        mode_lbl  = "DRY RUN" if dry_run else "LIVE — making calls"
        print(f"\n📞 AI Voice Calling ({mode_lbl})…\n")
        from modules.caller import run_caller, is_calling_window, get_central_time_str
        print(f"  Current time : {get_central_time_str()}")
        allowed, reason = is_calling_window()
        print(f"  Window check : {reason}\n")
        result = run_caller(limit=limit, dry_run=dry_run, force=force, min_score=min_score)
        print(f"\n Done.")
        print(f"  Called  : {result['called']}")
        print(f"  Failed  : {result['failed']}")
        print(f"  Skipped : {result['skipped']}")
        if result.get('reason'):
            print(f"  Reason  : {result['reason']}")
        print()
        print_stats()

    # ── View: call history ────────────────────────────────
    elif command == "calls":
        from modules.caller import print_call_history
        print_call_history()

    # ── Utility ───────────────────────────────────────────
    elif command == "replies":
        init_db()
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT business_name, city, phone, lead_score,
                   reply_intent, last_reply_at, outreach_status
            FROM leads
            WHERE reply_intent IS NOT NULL
            ORDER BY last_reply_at DESC LIMIT 50
        """)
        rows = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
        conn.close()
        if not rows:
            print("\n  No replies yet.\n")
        else:
            print(f"\n── {len(rows)} replies received ─────────────────────────")
            print(f"  {'#':<4} {'Business':<28} {'City':<14} {'Intent':<16} {'Score':<7} {'Status'}")
            print("  " + "─"*85)
            for i, r in enumerate(rows, 1):
                intent_icons = {
                    'interested':'HOT',
                    'question':'Q?',
                    'not_interested':'---',
                    'stop':'STP',
                    'unknown':'???'
                }
                icon = intent_icons.get(r['reply_intent'], '   ')
                print(f"  {i:<4} {(r['business_name'] or '')[:27]:<28} "
                      f"{(r['city'] or '')[:13]:<14} "
                      f"{icon} {(r['reply_intent'] or '')[:12]:<16} "
                      f"{(r['lead_score'] or 0):<7} {r['outreach_status']}")
            print()

    elif command == "reply-server":
        print("\n Starting reply handler on port 5001…")
        print(" Run ngrok in another terminal: ngrok http 5001\n")
        os.system("python reply_handler.py")

    elif command == "stats":
        print_stats()

    elif command == "leads":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        print_leads(limit)

    elif command == "init":
        init_db()
        print("\n Database initialised.\n")

    else:
        print(__doc__)


if __name__ == "__main__":
    import json
    main()
