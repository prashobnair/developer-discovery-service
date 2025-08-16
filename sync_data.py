# sync_data.py
import logging
import time
import argparse
from datetime import datetime, timezone
from config import CONFIG
from utils import ensure_dirs
from persistence import Persistence
from github_client import TokenManager, GitHubClient
from pipeline import (
    discover_developers_via_repos_flow,
    collect_repos_and_prs_for_new_users_flow,
    update_existing_users_flow,
    fetch_diffs_parallel_flow
)

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def sync(skip_user_discovery: bool = False):
    """Runs the full data synchronization pipeline."""
    start_time = time.time()
    logger.info("🚀 === Starting Data Collection === 🚀")

    ensure_dirs(CONFIG)
    if not CONFIG.get('github', {}).get('tokens'):
        logger.error("❌ GITHUB_TOKENS not set. Exiting.")
        return

    persistence = Persistence(db_path=CONFIG['outputs']['db_path'])
    token_manager = TokenManager(tokens=CONFIG['github']['tokens'])
    gh_client = GitHubClient(token_manager, CONFIG)
    
    last_run_timestamp = persistence.get_meta('last_successful_run')
    successful_run = False

    try:
        
        if not skip_user_discovery:
            discover_developers_via_repos_flow(gh_client, persistence, CONFIG)
        else:
            logger.warning("Skipping discovery stage as requested by --skip-discovery flag.")
        
        #collect_repos_and_prs_for_new_users_flow(gh_client, persistence, CONFIG)
        #update_existing_users_flow(gh_client, persistence, last_run_timestamp)
        #fetch_diffs_parallel_flow(gh_client, persistence, CONFIG)
        
        successful_run = True
    except Exception as e:
        logger.critical(f"A critical error occurred during data sync: {e}", exc_info=True)
    finally:
        if successful_run:
            new_timestamp = datetime.now(timezone.utc).isoformat()
            persistence.set_meta('last_successful_run', new_timestamp)
            logger.info(f"✅ === Data Sync Finished Successfully in {time.time() - start_time:.2f}s ===")
        else:
            logger.error(f"❌ === Data Sync Failed. Timestamp not updated. ===")

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Run the data sync pipeline.")
    parser.add_argument(
        "--skip-user-discovery",
        action="store_true", 
        help="Skip the initial repository user discovery stage and resume with collection."
    )
    args = parser.parse_args()
    
    sync(skip_user_discovery=args.skip_user_discovery)