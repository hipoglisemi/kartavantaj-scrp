"""
URL Health Checker

Checks all active campaign tracking_url values by following redirects and
detecting broken/incorrect destinations:
- 404 errors
- Redirects to homepage (not a specific campaign page)
- Connection timeouts
- URLs that redirect to a completely different domain

Reports broken URLs and optionally marks campaigns as inactive.
"""

import os
import sys
import time
import requests
from urllib.parse import urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import get_db_session
from src.models import Campaign

# Known "homepage" paths — if the final URL ends up here, the URL is broken
HOMEPAGE_PATHS = {
    "/",
    "/kampanyalar",
    "/kampanyalar/",
    "/default.aspx",
    "/home",
    "/index",
    "",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/121.0.0.0 Safari/537.36"
}

REQUEST_TIMEOUT = 15


def _check_url(url: str) -> dict:
    """Follow redirects and classify the URL health."""
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        final_url = resp.url
        status = resp.status_code
        parsed_original = urlparse(url)
        parsed_final = urlparse(final_url)

        # 404 or server error
        if status >= 400:
            return {"ok": False, "reason": f"HTTP {status}", "final_url": final_url}

        # Redirected to a completely different domain
        if parsed_final.netloc and parsed_original.netloc:
            orig_domain = parsed_original.netloc.replace("www.", "")
            final_domain = parsed_final.netloc.replace("www.", "")
            if orig_domain != final_domain:
                return {
                    "ok": False,
                    "reason": f"Domain changed: {orig_domain} → {final_domain}",
                    "final_url": final_url,
                }

        # Redirected to homepage
        final_path = parsed_final.path.rstrip("/")
        if final_path in HOMEPAGE_PATHS or final_path == "" :
            return {
                "ok": False,
                "reason": f"Redirected to homepage ({final_url})",
                "final_url": final_url,
            }

        return {"ok": True, "final_url": final_url, "status": status}

    except requests.exceptions.Timeout:
        return {"ok": False, "reason": "Timeout", "final_url": url}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "reason": f"Connection error: {e}", "final_url": url}
    except Exception as e:
        return {"ok": False, "reason": str(e), "final_url": url}


def run_url_health_check(deactivate_broken: bool = False):
    print("🚀 Starting URL Health Checker...")
    print(f"   ⚙️  Auto-deactivate broken campaigns: {deactivate_broken}")

    broken = []
    ok_count = 0

    try:
        with get_db_session() as db:
            campaigns = db.query(Campaign).filter(
                Campaign.is_active == True,
                Campaign.tracking_url != None,
            ).all()

            print(f"   📊 Checking {len(campaigns)} active campaigns...\n")

            for i, c in enumerate(campaigns, 1):
                url = c.tracking_url
                result = _check_url(url)

                if result["ok"]:
                    ok_count += 1
                    if i % 20 == 0:
                        print(f"   [{i}/{len(campaigns)}] ...{ok_count} OK so far")
                else:
                    broken.append({
                        "id": c.id,
                        "title": c.title,
                        "url": url,
                        "reason": result["reason"],
                        "final_url": result.get("final_url", ""),
                    })
                    print(f"   ❌ [{c.id}] {c.title[:50]}")
                    print(f"      URL    : {url}")
                    print(f"      Reason : {result['reason']}")
                    print(f"      Final  : {result.get('final_url', '')}")

                    if deactivate_broken:
                        c.is_active = False
                        db.commit()
                        print(f"      ⛔ Deactivated.")

                # Be polite — 2 requests/sec max
                time.sleep(0.5)

            print(f"\n{'='*60}")
            print(f"🏁 URL Health Check complete.")
            print(f"   ✅ OK      : {ok_count}")
            print(f"   ❌ Broken  : {len(broken)}")
            print(f"{'='*60}")

            if broken:
                print("\n📋 Broken campaign summary:")
                for b in broken:
                    print(f"   [{b['id']}] {b['title'][:50]}")
                    print(f"         {b['url']}")
                    print(f"         → {b['reason']}")

    except Exception as e:
        print(f"\n📛 CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deactivate",
        action="store_true",
        help="Automatically set is_active=False for campaigns with broken URLs",
    )
    args = parser.parse_args()
    run_url_health_check(deactivate_broken=args.deactivate)
