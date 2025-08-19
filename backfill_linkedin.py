#backfill_linkedin.py
import logging
import time
import argparse
import requests
import re # <-- Import the regular expression module
from bs4 import BeautifulSoup
from tqdm import tqdm
from typing import Optional
from config import CONFIG
from persistence import Persistence

# --- Logging Setup ---
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

def scrape_linkedin_url(github_profile_url: str) -> Optional[str]:
    """
    Scrapes the user's public GitHub page to find their LinkedIn profile URL
    using a robust regular expression search.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(github_profile_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"Failed to fetch profile {github_profile_url} with status code {response.status_code}")
            return None
        
        # --- THIS IS THE NEW, ROBUST LOGIC ---
        # Treat the entire page content as text and search for the LinkedIn URL pattern.
        # This pattern looks for "linkedin.com/in/" followed by valid profile characters.
        html_content = response.text
        match = re.search(r'href="(https?://(www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+/?)"', html_content)
        
        if match:
            # The first group of the match will be the full URL.
            return match.group(1)
        # --- END OF CORRECTION ---
            
    except requests.RequestException as e:
        logger.warning(f"Could not scrape {github_profile_url}: {e}")
    return None

def backfill(limit: Optional[int] = None):
    """
    Finds users without a LinkedIn URL, scrapes their GitHub profile,
    and updates the database if a URL is found.
    """
    start_time = time.time()
    logger.info("🚀 === Starting LinkedIn Backfill Process === 🚀")

    persistence = Persistence(db_path=CONFIG['outputs']['db_path'])

    users_to_check = persistence.get_users_without_linkedin()
    if not users_to_check:
        logger.info("No users found without a LinkedIn URL. All set!")
        return

    logger.info(f"Found {len(users_to_check)} users to check for LinkedIn profiles.")
    
    if limit:
        users_to_check = users_to_check[:limit]
        logger.info(f"Applying limit, will only process {len(users_to_check)} users.")

    found_count = 0
    for user in tqdm(users_to_check, desc="Backfilling LinkedIn URLs"):
        linkedin_url = scrape_linkedin_url(user['html_url'])
        
        if linkedin_url:
            persistence.update_user_linkedin(user['login'], linkedin_url)
            logger.info(f"SUCCESS: Found LinkedIn for {user['login']} -> {linkedin_url}")
            found_count += 1
        
        time.sleep(1.5)

    logger.info(f"✅ === Backfill Finished in {time.time() - start_time:.2f} seconds ===")
    logger.info(f"Found and updated {found_count} new LinkedIn profiles.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing LinkedIn URLs by scraping GitHub profiles.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional: Limit the number of users to process (e.g., for a test run)."
    )
    args = parser.parse_args()
    
    backfill(limit=args.limit)