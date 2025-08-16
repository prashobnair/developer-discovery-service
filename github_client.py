# github_client.py
import os
import requests
import time
import logging
import threading
from github import Github, GithubException, RateLimitExceededException
from typing import List, Optional, Dict, Any, Tuple
from utils import extract_contact_info
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

def _load_graphql_query(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "queries", f"{name}.graphql")
    with open(path, "r") as f:
        return f.read()

REPO_GRAPHQL_QUERY = _load_graphql_query("GetUserRepos")
PR_METADATA_GRAPHQL_QUERY = _load_graphql_query("GetPrMetadata")
REPO_DETAILS_GRAPHQL_QUERY = _load_graphql_query("GetRepoDetails")

class TokenManager:
    def __init__(self, tokens: List[str]):
        if not tokens: raise ValueError("No GitHub tokens provided.")
        self.tokens = tokens
        self.clients = [Github(t.strip(), per_page=100) for t in tokens]
        self.index = 0
        self.lock = threading.Lock()

    def get_client_and_token(self) -> Tuple[Github, str]:
        with self.lock:
            return self.clients[self.index], self.tokens[self.index]

    def switch_token(self):
        with self.lock:
            self.index = (self.index + 1) % len(self.tokens)
            logger.info(f"Switched to token index {self.index}")

class GitHubClient:
    def __init__(self, token_mgr: TokenManager, config: dict):
        self.token_mgr = token_mgr
        self.config = config

    def _graphql_query(self, query: str, variables: dict) -> dict:
        _, token = self.token_mgr.get_client_and_token()
        headers = {"Authorization": f"bearer {token}", "Accept": "application/vnd.github.v4+json"}
        for attempt in range(self.config['github']['diff_fetch_retry']):
            try:
                resp = requests.post(self.config['github']['graphql_url'], json={"query": query, "variables": variables}, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if "errors" in data:
                    if any("RATE_LIMITED" in e.get("type", "") for e in data.get("errors", [])):
                        logger.warning("GraphQL rate limit exceeded. Switching token.")
                        self.token_mgr.switch_token()
                        time.sleep(self.config['github']['diff_fetch_backoff'])
                        continue
                    logger.warning(f"GraphQL returned errors: {data['errors']}")
                return data
            except requests.exceptions.RequestException as e:
                logger.error(f"GraphQL request failed (attempt {attempt+1}): {e}")
                time.sleep(self.config['github']['diff_fetch_backoff'] ** attempt)
        return {}
    
    def get_user_details(self, login: str) -> Optional[Dict]:
        client, _ = self.token_mgr.get_client_and_token()
        try:
            user = client.get_user(login)
            # Initialize with the basic info we always need
            details = {
                "login": user.login,
                "html_url": user.html_url,
                "location": user.location,
                "email": user.email, # Start with the public email field
                "linkedin_url": None
            }

            # --- Smart Extraction Logic ---
            # We still check the user's blog and bio, but we don't store them.
            
            # 1. Check if the blog URL is a LinkedIn profile
            if user.blog and "linkedin.com/in/" in user.blog:
                details["linkedin_url"] = user.blog
            
            # 2. If no LinkedIn found yet, parse the bio text for it.
            #    Also, parse the bio for an email if the public one is missing.
            if user.bio and (not details["linkedin_url"] or not details["email"]):
                bio_contacts = extract_contact_info(user.bio)
                if not details["linkedin_url"] and bio_contacts["linkedin_url"]:
                    details["linkedin_url"] = bio_contacts["linkedin_url"]
                if not details["email"] and bio_contacts["email"]:
                    details["email"] = bio_contacts["email"]

            return details
            
        except RateLimitExceededException:
            logger.warning(f"REST API rate limit on get_user for {login}. Switching token.")
            self.token_mgr.switch_token()
            return self.get_user_details(login)
        except GithubException as e:
            if e.status == 404:
                logger.debug(f"User {login} not found (404).")
            else:
                logger.error(f"GitHub API error getting user {login}: {e}")
            return None

    def search_repositories(self, query: str) -> List[Any]:
        client, _ = self.token_mgr.get_client_and_token()
        repos = []
        try:
            results = client.search_repositories(query, sort="stars", order="desc")
            for i, repo in enumerate(results):
                if i >= 1000: break
                repos.append(repo)
        except RateLimitExceededException:
            logger.warning(f"REST API rate limit on repo search. Switching token.")
            self.token_mgr.switch_token()
            return self.search_repositories(query)
        except GithubException as e:
            logger.error(f"GitHub API error searching repositories with query '{query}': {e}")
        return repos

    def fetch_user_repos_graphql(self, username: str) -> List[dict]:
        repos, after_cursor = [], None
        max_repos = self.config['pipeline']['max_repos_per_user']
        while True:
            variables = {"username": username, "pageSize": self.config['github']['graphql_page_size'], "after": after_cursor}
            res = self._graphql_query(REPO_GRAPHQL_QUERY, variables)
            user_data = res.get("data", {}).get("user")
            if not user_data or not user_data.get("repositories"): break
            for node in user_data["repositories"].get("nodes", []):
                readme_text = ((node.get("readme_md") or {}).get("text") or (node.get("readme_rst") or {}).get("text") or (node.get("readme") or {}).get("text"))
                repos.append({
                    "repo_full_name": node.get("nameWithOwner"), "description": node.get("description"),
                    "stars": node.get("stargazerCount", 0),
                    "topics": [t['topic']['name'] for t in node.get("repositoryTopics", {}).get("nodes", []) if t.get('topic')],
                    "readme": readme_text[:self.config['github']['max_readme_bytes']] if readme_text else None
                })
                if max_repos and len(repos) >= max_repos: return repos
            page_info = user_data["repositories"].get("pageInfo", {})
            if page_info.get("hasNextPage"): after_cursor = page_info.get("endCursor")
            else: break
        return repos

    def search_prs_by_author(self, login: str, since_date: Optional[str] = None) -> List[Any]:
        client, _ = self.token_mgr.get_client_and_token()
        prs = []
        try:
            query = f"is:pr author:{login} is:public"
            if since_date:
                query += f" created:>{since_date}"
            # For initial bulk fetches, apply the max_pr_age_days limit if it exists
            else:
                max_age_days = self.config.get('pipeline', {}).get('max_pr_age_days')
                if max_age_days:
                    # Calculate the cutoff date (e.g., 270 days ago)
                    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
                    # Format as YYYY-MM-DD for the GitHub API
                    cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
                    query += f" created:>{cutoff_date_str}"

            results = client.search_issues(query, sort="created", order="desc")
            max_prs = self.config['pipeline']['max_pr_results_per_user']
            for i, issue in enumerate(results):
                if max_prs and i >= max_prs: break
                prs.append(issue)
        except RateLimitExceededException:
            logger.warning(f"REST API rate limit on PR search for {login}. Switching token.")
            self.token_mgr.switch_token()
            return self.search_prs_by_author(login, since_date)
        except GithubException as e:
            logger.error(f"Error searching PRs for user {login}: {e}")
        return prs

    def fetch_pr_metadata_graphql(self, repo_full: str, number: int) -> Optional[dict]:
        try: owner, name = repo_full.split("/", 1)
        except ValueError: return None
        variables = {"owner": owner, "name": name, "number": int(number)}
        res = self._graphql_query(PR_METADATA_GRAPHQL_QUERY, variables)
        return res.get("data", {}).get("repository", {}).get("pullRequest")

    def fetch_pull_diff_http(self, repo_full: str, number: int, save_path: str) -> bool:
        _, token = self.token_mgr.get_client_and_token()
        url = f"https://api.github.com/repos/{repo_full}/pulls/{number}"
        headers = {"Accept": "application/vnd.github.v3.diff", "Authorization": f"token {token}"}
        try:
            resp = requests.get(url, headers=headers, timeout=45)
            if resp.status_code == 404: return False
            resp.raise_for_status()
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f: f.write(resp.content)
            return True
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP error fetching diff for {repo_full}#{number}: {e}")
        except Exception as e:
            logger.error(f"Exception fetching diff for {repo_full}#{number}: {e}")
        return False

    def fetch_repo_details_batch(self, repo_full_names: List[str]) -> Dict[str, Dict]:
        """
        Fetches details (desc, topics, readme) for a list of external repos.
        It processes them in a batch but makes individual GraphQL calls.
        """
        details_map = {}
        readme_limit = self.config.get('enrichment_strategy', {}).get('fetch_readme_bytes', 0)

        for name in repo_full_names:
            try:
                owner, repo_name = name.split('/', 1)
                variables = {"owner": owner, "name": repo_name}
                
                # Make individual GraphQL call for each repo
                res = self._graphql_query(REPO_DETAILS_GRAPHQL_QUERY, variables)
                repo_data = res.get("data", {}).get("repository")

                if repo_data:
                    topics = [
                        t['topic']['name'] for t in repo_data.get("repositoryTopics", {}).get("nodes", []) if t.get('topic')
                    ]
                    
                    readme_text = None
                    if readme_limit > 0 and repo_data.get('readme'):
                        readme_text = repo_data['readme'].get('text', '')[:readme_limit]

                    details_map[name] = {
                        "description": repo_data.get('description'),
                        "topics": topics,
                        "readme": readme_text
                    }
                else:
                    details_map[name] = {} # Cache failure

            except Exception as e:
                logger.warning(f"Could not fetch details for repo {name}: {e}")
                details_map[name] = {} # Cache failure
            
            time.sleep(1) # Respectful pause between each enrichment call
        
        return details_map