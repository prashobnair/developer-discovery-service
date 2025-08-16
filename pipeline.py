# pipeline.py
import logging
import os
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import Tuple, Optional

from persistence import Persistence
from github_client import GitHubClient
from utils import normalize, text_matches_keywords

logger = logging.getLogger(__name__)

def discover_developers_via_repos_flow(gh_client: GitHubClient, persistence: Persistence, config: dict):
    logger.info("--- Stage 1: Discovering New Developers via Partitioned Repo Search ---")
    
    # Pre-load all existing users from the database to avoid re-processing them.
    logger.info("Pre-loading existing user logins from the database...")
    existing_user_logins = persistence.get_all_user_logins()
    logger.info(f"Loaded {len(existing_user_logins)} existing users. Will only process new discoveries.")

    search_config = config.get('sync_config', {}).get('repository_search', {})
    primary_keywords = search_config.get('primary_keywords', [])
    star_partitions = search_config.get('star_partitions', [])
    target_locations = {loc.lower() for loc in config.get('sync_config', {}).get('target_locations', [])}
    
    discovered_logins_this_run = set()

    for keyword in primary_keywords:
        for star_range in star_partitions:
            query = (
                f'"{keyword}" in:readme,description,topics '
                f'stars:{star_range} '
                f'forks:>{search_config.get("min_forks", 0)}'
            )
            
            repositories = gh_client.search_repositories(query)
            logger.info(f"Found {len(repositories)} potential repos for this partition.")

            for repo in tqdm(repositories, desc=f"Processing '{keyword}' (stars: {star_range})"):
                # 1. Create a list of potential developers to check for this repo
                devs_to_check = []
                if hasattr(repo, 'owner') and repo.owner:
                    devs_to_check.append(repo.owner)

                try:
                    contributors = repo.get_contributors()

                    for contributor in contributors:
                        devs_to_check.append(contributor)
                except Exception as e:
                        logger.warning(f"Could not fetch contributors for {repo.full_name}: {e}")

                # 3. Process the owner and top contributors
                for dev in devs_to_check:
                    if not dev or not dev.login or dev.type != "User":
                        continue
                    if dev.login in existing_user_logins or dev.login in discovered_logins_this_run:
                        continue

                    user_details = gh_client.get_user_details(dev.login)
                    discovered_logins_this_run.add(dev.login)

                    if user_details and user_details.get("location"):
                        user_location = normalize(user_details["location"])
                        if any(loc in user_location for loc in target_locations):
                            logger.info(f"Found relevant developer: {dev.login} in location: {user_details['location']} via repo: {repo.full_name}")
                            persistence.upsert_users([user_details], user_details['location'])
                
            logger.info("Partition finished. Pausing for 15 seconds...")
            time.sleep(15)

    logger.info("New developer discovery finished.")

def collect_repos_and_prs_for_new_users_flow(gh_client: GitHubClient, persistence: Persistence, config: dict):
    logger.info("--- Stage 2: Collecting Full Profiles for New Users ---")
    
    # Aggregate all relevance keywords
    all_relevance_keywords = set()
    for client_cfg in config.get('clients', {}).values():
        for kw_list in client_cfg.get('relevance_keywords', {}).values():
            all_relevance_keywords.update(kw_list)
            
    while True:
        users_batch = persistence.get_unprocessed_users_batch(batch_size=10)
        if not users_batch: 
            logger.info("No new users to process.")
            break
        
        logger.info(f"Processing full history for batch of {len(users_batch)} new users...")
        for login in users_batch:
            repos_meta = gh_client.fetch_user_repos_graphql(login)
            relevant_repos = []
            for repo in repos_meta:
                if not repo.get("repo_full_name"): continue
                # Combine all text sources from the repo into one string
                match_text = (
                    f"{repo.get('repo_full_name', '')} "
                    f"{repo.get('description', '')} "
                    f"{' '.join(repo.get('topics', []))} "
                    f"{repo.get('readme', '')}"
                )
                
                # Find which specific keywords from our master list are present
                matched_keywords = []
                normalized_match_text = normalize(match_text)
                for keyword in all_relevance_keywords:
                    # Use padding to ensure we match whole words
                    if f" {keyword} " in f" {normalized_match_text} ":
                        matched_keywords.append(keyword)
                
                # If we found any matches, save the list of keywords
                if matched_keywords:
                    repo['matched_reasons'] = matched_keywords
                    relevant_repos.append(repo)
            
            if relevant_repos:
                persistence.add_repos(login, relevant_repos)

            prs = gh_client.search_prs_by_author(login)
            if prs: persistence.add_prs(login, prs)
            persistence.set_user_stage(login, 1)
        
        logger.info("New user batch finished. Pausing for 20 seconds...")
        time.sleep(20)
    logger.info("Full profile collection for new users finished.")

def update_existing_users_flow(gh_client: GitHubClient, persistence: Persistence, last_run_timestamp: Optional[str]):
    logger.info("--- Stage 3: Fetching Delta Updates for Existing Users ---")
    
    logger.info(f"Fetching new PRs created since {last_run_timestamp}")
    existing_users = persistence.get_processed_users()
    if not existing_users:
        logger.info("No existing users to update.")
        return
    
    # Create the master list of all keywords for checking repo relevance
    all_relevance_keywords = set()
    for client_cfg in gh_client.config.get('clients', {}).values():
        for kw_list in client_cfg.get('relevance_keywords', {}).values():
            all_relevance_keywords.update(kw_list)

    for login in tqdm(existing_users, desc="Updating existing users"):
        # --- DUTY 1: Fetch new PRs (Delta Update) ---
        if last_run_timestamp:
            new_prs = gh_client.search_prs_by_author(login, since_date=last_run_timestamp)
            if new_prs:
                logger.info(f"Found {len(new_prs)} new PRs for existing user {login}.")
                persistence.add_prs(login, new_prs)
        
        # --- DUTY 2: Find new Repositories (Full Scan) ---
        repos_meta = gh_client.fetch_user_repos_graphql(login)
        relevant_repos = []
        for repo in repos_meta:
            if not repo.get("repo_full_name"): continue
            
            match_text = (
                f"{repo.get('repo_full_name', '')} "
                f"{repo.get('description', '')} "
                f"{' '.join(repo.get('topics', []))} "
                f"{repo.get('readme', '')}"
            )
            
            matched_keywords = []
            normalized_match_text = normalize(match_text)
            for keyword in all_relevance_keywords:
                if f" {keyword} " in f" {normalized_match_text} ":
                    matched_keywords.append(keyword)
            
            if matched_keywords:
                repo['matched_reasons'] = matched_keywords
                relevant_repos.append(repo)

        if relevant_repos:
            # add_repos uses INSERT OR IGNORE, so it will only add new repos
            # and is safe to run repeatedly.
            persistence.add_repos(login, relevant_repos)

        time.sleep(1) # Small pause between each user
    
    logger.info("Updates for existing users finished.")

def _fetch_one_diff_worker(pr_row: Tuple, gh_client: GitHubClient, persistence: Persistence) -> bool:
    time.sleep(random.uniform(0.5, 2.5))
    pr_id, login, repo_full, number = pr_row
    diff_path = os.path.join(gh_client.config['outputs']['diffs_dir'], login, f"{repo_full.replace('/', '__')}_pr{number}.diff")
    try:
        if not gh_client.fetch_pull_diff_http(repo_full, number, diff_path):
            return False
        metadata = gh_client.fetch_pr_metadata_graphql(repo_full, number)
        if metadata: persistence.update_pr_diff_metrics(pr_id, diff_path, metadata)
        return True
    except Exception as e:
        logger.error(f"Error in diff worker for PR ID {pr_id}: {e}")
        return False

def fetch_diffs_parallel_flow(gh_client: GitHubClient, persistence: Persistence, config: dict):
    logger.info("--- Stage 4: Fetching Diffs and Metadata ---")
    prs_to_fetch = persistence.get_prs_without_diff()
    if not prs_to_fetch:
        logger.info("No new PR diffs to fetch.")
        return
    
    logger.info(f"Fetching diffs for {len(prs_to_fetch)} PRs...")
    with ThreadPoolExecutor(max_workers=config['pipeline']['diff_fetch_workers']) as executor:
        futures = {executor.submit(_fetch_one_diff_worker, pr, gh_client, persistence): pr for pr in prs_to_fetch}
        for future in tqdm(as_completed(futures), total=len(prs_to_fetch), desc="Fetching PR Diffs"):
            try:
                future.result()
            except Exception as e:
                logger.error(f"A diff fetch worker generated an exception: {e}")

    logger.info("Diff fetching finished.")