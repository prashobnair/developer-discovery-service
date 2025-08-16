# scoring.py
import logging
import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Set

logger = logging.getLogger(__name__)

# --- Scoring Model Constants ---
ACTIVITY_HALF_LIFE_DAYS = 365.0
LAMBDA_DECAY = np.log(2) / ACTIVITY_HALF_LIFE_DAYS

WEIGHT_EXPERTISE = 0.25
WEIGHT_IMPACT = 0.50
WEIGHT_ACTIVITY = 0.25

TIER_CUTOFFS = {"S": 0.95, "A": 0.80, "B": 0.60, "C": 0.40, "D": 0.20}

def assign_tier(score: float, quantiles: Dict[str, float]) -> str:
    """Assigns a tier based on a score and pre-calculated quantiles."""
    if score >= quantiles["S"]: return "S"
    if score >= quantiles["A"]: return "A"
    if score >= quantiles["B"]: return "B"
    if score >= quantiles["C"]: return "C"
    if score >= quantiles["D"]: return "D"
    return "F"

def is_pr_relevant(
    pr: Dict[str, Any],
    dev_relevant_repos: Set[str],
    client_keywords: Set[str],
    external_repo_details: Dict[str, Dict] # <-- Use the new, richer details cache
) -> bool:
    """Determines if a PR is relevant to the client using a multi-factor check."""
    target_repo_name = pr.get('repo_full_name', '')
    if not target_repo_name:
        return False

    # 1. Primary Check: Is this PR to one of the developer's own, pre-vetted relevant repos?
    if target_repo_name in dev_relevant_repos:
        return True
    
    # 2. Secondary Check: For external repos, check the cached details.
    details = external_repo_details.get(target_repo_name, {})
    
    # Check topics first as they are high-signal
    topics = details.get('topics', [])
    if any(kw in topics for kw in client_keywords):
        return True

    # Build a larger text block from name, description, and readme to search
    search_corpus = (
        f"{target_repo_name} "
        f"{details.get('description', '')} "
        f"{details.get('readme', '')}"
    ).lower()

    if any(kw in search_corpus for kw in client_keywords):
        return True
    
    return False

def calculate_scores_and_tiers(
    dev_data: Dict[str, Any],
    primary_keywords: List[str],
    relevance_keywords: Dict[str, List[str]],
    external_repo_details: Dict[str, Dict] # <-- New parameter
) -> List[Dict[str, Any]]:
    if not dev_data:
        logger.warning("No developer data provided for scoring.")
        return []

    logger.info(f"Starting scoring for {len(dev_data)} developers.")
    
    df = pd.DataFrame.from_dict(dev_data, orient='index')
    if df.empty:
        logger.warning("DataFrame is empty after loading from dict.")
        return []
    df['login'] = df.index
    
    # Create a single set of all keywords this specific client cares about.
    client_keyword_set = set(primary_keywords)
    for kw_list in relevance_keywords.values():
        client_keyword_set.update(kw_list)

    # --- Expertise Score Calculation (Unchanged) ---
    def calc_expertise(repos_list: list) -> float:
        score = 0.0
        if not isinstance(repos_list, list): return score
        for repo in repos_list:
            try:
                repo_keywords = set(json.loads(repo.get('matched_reasons', '[]')))
            except (json.JSONDecodeError, TypeError):
                repo_keywords = set()
            if repo_keywords.intersection(client_keyword_set):
                score += np.log10(repo.get('stars', 0) + 1)
        return score
    df['expertise_raw'] = df['repos'].apply(calc_expertise)

    # --- Combined Impact and Activity Calculation ---
    def calc_impact_and_activity(row):
        # First, determine the set of this developer's own repos that are relevant to this client.
        dev_relevant_repos = set()
        for repo in row.get('repos', []):
            try:
                repo_keywords = set(json.loads(repo.get('matched_reasons', '[]')))
                if repo_keywords.intersection(client_keyword_set):
                    dev_relevant_repos.add(repo.get('repo_full_name'))
            except (json.JSONDecodeError, TypeError):
                continue

        impact_score = 0.0
        most_recent_relevant_date = None
        now = datetime.now(timezone.utc)
        own_repos_set = {r['repo_full_name'] for r in row.get('repos', [])}

        for pr in row.get('prs', []):
            # Use our new, more intelligent relevance check for each PR
            if is_pr_relevant(pr, dev_relevant_repos, client_keyword_set, external_repo_details):
                # Calculate Impact Score for this relevant PR
                w_merged = 1.5 if pr.get('merged') else 0.1
                is_external = pr.get('repo_full_name') not in own_repos_set
                w_external = 2.0 if is_external else 1.0
                lines_changed = pr.get('lines_added', 0) + pr.get('lines_removed', 0)
                impact_score += w_merged * w_external * (np.log10(lines_changed + 1))

                # Check for Activity Score using this relevant PR
                if pr.get('merged') and pr.get('merged_at'):
                    try:
                        pr_date = datetime.fromisoformat(str(pr['merged_at']).replace("Z", "+00:00"))
                        if most_recent_relevant_date is None or pr_date > most_recent_relevant_date:
                            most_recent_relevant_date = pr_date
                    except (ValueError, TypeError):
                        continue

        activity_score = 0.0
        if most_recent_relevant_date:
            delta_days = (now - most_recent_relevant_date).days
            activity_score = np.exp(-LAMBDA_DECAY * max(0, delta_days))
            
        return pd.Series([impact_score, activity_score], index=['impact_raw', 'activity_raw'])

    df[['impact_raw', 'activity_raw']] = df.apply(calc_impact_and_activity, axis=1)

    # --- Normalization and Tiering (Unchanged) ---
    for score_type in ['expertise', 'impact', 'activity']:
        raw_col = f'{score_type}_raw'
        norm_col = f'{score_type}_norm'
        max_val = df[raw_col].max()
        if max_val > 0:
            df[norm_col] = 100 * (df[raw_col] / max_val)
        else:
            df[norm_col] = 0
    df.fillna(0, inplace=True)
    
    df['final_score'] = (
        WEIGHT_EXPERTISE * df['expertise_norm'] +
        WEIGHT_IMPACT * df['impact_norm'] +
        WEIGHT_ACTIVITY * df['activity_norm']
    )

    quantiles = {tier: df['final_score'].quantile(q) for tier, q in TIER_CUTOFFS.items()}
    df['tier'] = df['final_score'].apply(assign_tier, quantiles=quantiles)
    
    logger.info(f"Tier distribution for this report:\n{df['tier'].value_counts().sort_index(ascending=False)}")

    result_cols = ['login', 'final_score', 'tier']
    return df[result_cols].to_dict('records')