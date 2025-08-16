# persistence.py
import sqlite3
import threading
import json
import os
import pandas as pd
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

class Persistence:
    _lock = threading.Lock()

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                login TEXT PRIMARY KEY, location TEXT, html_url TEXT,
                stage INTEGER DEFAULT -1, discovered_at TEXT,
                email TEXT, linkedin_url TEXT,
                expertise_score REAL, impact_score REAL, activity_score REAL,
                final_score REAL, tier TEXT
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_stage ON users (stage)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_tier ON users (tier)")

            c.execute("""
            CREATE TABLE IF NOT EXISTS repos (
                repo_full_name TEXT PRIMARY KEY, login TEXT,
                matched_reasons TEXT, stars INTEGER,
                FOREIGN KEY (login) REFERENCES users (login)
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_repos_login ON repos (login)")

            c.execute("""
            CREATE TABLE IF NOT EXISTS prs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT,
                repo_full_name TEXT, number INTEGER, html_url TEXT,
                diff_path TEXT, diff_fetched INTEGER DEFAULT 0,
                merged INTEGER DEFAULT 0, merged_at TEXT,
                files_changed INTEGER, lines_added INTEGER, lines_removed INTEGER,
                FOREIGN KEY (login) REFERENCES users (login),
                UNIQUE(login, repo_full_name, number)
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_prs_login ON prs (login)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_prs_diff_fetched ON prs (diff_fetched, id)")

            c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )""")

            c.execute("""
            CREATE TABLE IF NOT EXISTS client_scores (
                score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT,
                login TEXT,
                final_score REAL,
                tier TEXT,
                generated_at TEXT,
                FOREIGN KEY (login) REFERENCES users (login)
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_client_scores_client_login ON client_scores (client_name, login)")
            
            c.execute("""
            CREATE TABLE IF NOT EXISTS external_repo_details (
                repo_full_name TEXT PRIMARY KEY,
                details TEXT, -- Stored as a JSON blob
                fetched_at TEXT
            )""")
            self.conn.commit()
            
    def get_external_repo_details(self, repo_names: List[str]) -> Dict[str, Dict]:
        """Retrieves cached details for a list of external repositories."""
        if not repo_names: return {}
        placeholders = ','.join('?' for _ in repo_names)
        query = f"SELECT repo_full_name, details FROM external_repo_details WHERE repo_full_name IN ({placeholders})"
        with self._lock:
            c = self.conn.cursor()
            c.execute(query, repo_names)
            return {row[0]: json.loads(row[1]) for row in c.fetchall()}

    def save_external_repo_details(self, repo_details: Dict[str, Dict]):
        """Saves newly fetched details for external repos to the cache."""
        if not repo_details: return
        now = datetime.now(timezone.utc).isoformat()
        insert_data = [(name, json.dumps(details), now) for name, details in repo_details.items()]
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO external_repo_details (repo_full_name, details, fetched_at) VALUES (?, ?, ?)",
                insert_data
            )
            self.conn.commit()

    def upsert_users(self, users: List[Dict[str, Any]], location: str):
        now = datetime.now(timezone.utc).isoformat()
        user_data = [(u['login'], location, u.get('html_url'), now, u.get('email'), u.get('linkedin_url')) for u in users if u.get('login')]
        if not user_data: return
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO users (login, location, html_url, discovered_at, email, linkedin_url) VALUES (?, ?, ?, ?, ?, ?)",
                user_data
            )
            self.conn.commit()

    def add_repos(self, login: str, repos: List[Dict[str, Any]]):
        """
        Batch upserts relevant repositories for a user, merging keywords
        for existing repos.
        """
        if not repos: return

        with self._lock:
            c = self.conn.cursor()
            for repo in repos:
                repo_full_name = repo['repo_full_name']
                new_keywords = set(repo.get('matched_reasons', []))
                stars = repo.get('stars', 0)

                # 1. Check if the repo already exists
                c.execute("SELECT matched_reasons FROM repos WHERE repo_full_name=?", (repo_full_name,))
                result = c.fetchone()
                
                if result:
                    # 2. If it exists, merge the new keywords with the old ones
                    try:
                        existing_keywords = set(json.loads(result[0]))
                    except (json.JSONDecodeError, TypeError):
                        existing_keywords = set()
                    
                    merged_keywords = existing_keywords.union(new_keywords)
                    
                    # 3. Update the existing record with the merged list
                    c.execute(
                        "UPDATE repos SET matched_reasons=?, stars=? WHERE repo_full_name=?",
                        (json.dumps(list(merged_keywords)), stars, repo_full_name)
                    )
                else:
                    # 4. If it's a new repo, insert it
                    c.execute(
                        "INSERT INTO repos (repo_full_name, login, matched_reasons, stars) VALUES (?, ?, ?, ?)",
                        (repo_full_name, login, json.dumps(list(new_keywords)), stars)
                    )
            
            self.conn.commit()

    def add_prs(self, login: str, prs: List[Any]):
        pr_data = [(login, p.repository.full_name, p.number, p.html_url) for p in prs if hasattr(p, 'repository') and p.repository]
        if not pr_data: return
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO prs (login, repo_full_name, number, html_url) VALUES (?, ?, ?, ?)",
                pr_data
            )
            self.conn.commit()

    def get_all_user_logins(self) -> set:
        """Fetches all user logins from the DB for fast, in-memory checks."""
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT login FROM users")
            return {row[0] for row in c.fetchall()}
            
    def get_unprocessed_users_batch(self, batch_size: int = 100) -> List[str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT login FROM users WHERE stage=0 LIMIT ?", (batch_size,))
            return [row[0] for row in c.fetchall()]

    def set_user_stage(self, login: str, stage: int):
        with self._lock:
            self.conn.execute("UPDATE users SET stage=? WHERE login=?", (stage, login))
            self.conn.commit()

    def get_prs_without_diff(self, limit: Optional[int] = None) -> List[Tuple]:
        query = "SELECT id, login, repo_full_name, number FROM prs WHERE diff_fetched=0 AND repo_full_name IS NOT NULL ORDER BY id"
        if limit: query += f" LIMIT {int(limit)}"
        with self._lock:
            c = self.conn.cursor()
            c.execute(query)
            return c.fetchall()

    def update_pr_diff_metrics(self, pr_id: int, diff_path: str, metrics: dict):
        with self._lock:
            self.conn.execute(
                """UPDATE prs SET
                   diff_path=?, diff_fetched=1, files_changed=?, lines_added=?,
                   lines_removed=?, merged=?, merged_at=? WHERE id=?""",
                (diff_path, metrics.get('changedFiles', 0), metrics.get('additions', 0),
                 metrics.get('deletions', 0), 1 if metrics.get('merged') else 0,
                 metrics.get('mergedAt'), pr_id)
            )
            self.conn.commit()
    
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT value FROM meta WHERE key=?", (key,))
            result = c.fetchone()
            return result[0] if result else None

    def set_meta(self, key: str, value: str):
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()

    def get_processed_users(self) -> List[str]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT login FROM users WHERE stage=1")
            return [row[0] for row in c.fetchall()]

    def export_for_scoring(self) -> Dict[str, Any]:
        with self._lock:
            users_df = pd.read_sql_query("SELECT login FROM users WHERE stage=1", self.conn)
            if users_df.empty: return {}
            repos_df = pd.read_sql_query("SELECT login, repo_full_name, stars FROM repos", self.conn)
            prs_df = pd.read_sql_query("SELECT login, repo_full_name, merged, merged_at, lines_added, lines_removed FROM prs WHERE diff_fetched=1", self.conn)
        output = {login: {"repos": [], "prs": []} for login in users_df['login']}
        repos_grouped = repos_df.groupby('login')
        for login, group in repos_grouped:
            if login in output:
                output[login]['repos'] = group.to_dict('records')
        prs_grouped = prs_df.groupby('login')
        for login, group in prs_grouped:
            if login in output:
                output[login]['prs'] = group.to_dict('records')
        return output
    
    def save_client_report(self, client_name: str, report_data: List[Dict[str, Any]]):
        if not report_data: return
        now = datetime.now(timezone.utc).isoformat()
        insert_data = [(client_name, r['login'], r['final_score'], r['tier'], now) for r in report_data]
        with self._lock:
            self.conn.execute("DELETE FROM client_scores WHERE client_name=?", (client_name,))
            self.conn.executemany(
                "INSERT INTO client_scores (client_name, login, final_score, tier, generated_at) VALUES (?, ?, ?, ?, ?)",
                insert_data
            )
            self.conn.commit()