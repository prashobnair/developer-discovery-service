# generate_report.py
import logging
import time
import argparse
from config import CONFIG
from persistence import Persistence
from scoring import calculate_scores_and_tiers
from github_client import TokenManager, GitHubClient

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

def generate(client_name: str):
    start_time = time.time()
    logger.info(f"--- Generating report for client: {client_name} ---")

    client_config = CONFIG.get('clients', {}).get(client_name)
    if not client_config:
        logger.error(f"Client '{client_name}' not found in config.yaml.")
        available_clients = ", ".join(CONFIG.get('clients', {}).keys())
        logger.error(f"Available clients are: {available_clients}")
        return

    persistence = Persistence(db_path=CONFIG['outputs']['db_path'])
    logger.info("Loading developer data from local database...")
    all_dev_data = persistence.export_for_scoring()
    
    if not all_dev_data:
        logger.warning("No developer data found in the database. Run the sync script first.")
        return
    logger.info(f"Loaded data for {len(all_dev_data)} developers.")

    # --- "JUST-IN-TIME" ENRICHMENT STEP ---
    # 1. Find all unique external repo names mentioned in PRs
    external_repos_to_check = set()
    for login, data in all_dev_data.items():
        own_repos = {r['repo_full_name'] for r in data.get('repos', [])}
        for pr in data.get('prs', []):
            target_repo = pr.get('repo_full_name')
            if target_repo and target_repo not in own_repos:
                external_repos_to_check.add(target_repo)
    
    # 2. Check which ones are already in our cache (now using the new table)
    cached_details = persistence.get_external_repo_details(list(external_repos_to_check))
    repos_to_fetch = [name for name in external_repos_to_check if name not in cached_details]
    
    # 3. If there are any new ones, fetch them via the API
    if repos_to_fetch:
        logger.info(f"Found {len(repos_to_fetch)} new external repos. Fetching their details for enrichment...")
        
        # We need a GitHub client for this enrichment step
        token_manager = TokenManager(tokens=CONFIG['github']['tokens'])
        gh_client = GitHubClient(token_manager, CONFIG)
        newly_fetched_details = gh_client.fetch_repo_details_batch(repos_to_fetch)
        
        # 4. Save the new details to our cache for next time
        if newly_fetched_details:
            persistence.save_external_repo_details(newly_fetched_details)
            cached_details.update(newly_fetched_details)
        
        logger.info("External repo enrichment complete.")
    else:
        logger.info("All external repo details were already in the cache.")
    # --- END OF ENRICHMENT STEP ---

    # 5. Run scoring, now passing the enriched details
    primary_keywords = client_config.get('primary_keywords', [])
    relevance_keywords = client_config.get('relevance_keywords', {})
    
    report_data = calculate_scores_and_tiers(all_dev_data, primary_keywords, relevance_keywords, cached_details)
    
    if report_data:
        logger.info(f"Saving {len(report_data)} scored results for {client_name} to the database.")
        persistence.save_client_report(client_name, report_data)
    
    logger.info(f"--- ✅ Report for {client_name} finished in {time.time() - start_time:.2f}s ---")
    logger.info(f"To view results, query the 'client_scores' table in '{CONFIG['outputs']['db_path']}'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a scored report for a specific client.")
    parser.add_argument("client_name", type=str, help="The name of the client as defined in config.yaml (e.g., 'client_a_ai_eng').")
    args = parser.parse_args()
    
    generate(args.client_name)