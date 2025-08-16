# config.py
import os
import yaml
from typing import Dict, Any, List
from dotenv import load_dotenv

def _load_env_tokens() -> List[str]:
    # Try loading .env from repo root to avoid CWD issues
    repo_root = os.path.dirname(__file__)
    load_dotenv(os.path.join(repo_root, ".env"))
    load_dotenv()  # also allow current working directory

    raw = (
        os.getenv("GITHUB_TOKENS")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or ""
    )
    # Accept comma, newline or space separated
    separators = [",", "\n", " "]
    for sep in separators[1:]:
        raw = raw.replace(sep, separators[0])
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    return tokens

def load_and_prepare_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Loads configuration and prepares it by creating a unified set of
    keywords for the data sync process.
    """
    # Load YAML
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # --- Prepare config for the main data sync ---
    all_primary_keywords = set()
    if 'clients' in config:
        for client_config in config['clients'].values():
            all_primary_keywords.update(client_config.get('primary_keywords', []))
    
    if 'sync_config' in config and 'repository_search' in config['sync_config']:
        config['sync_config']['repository_search']['primary_keywords'] = sorted(list(all_primary_keywords))

    # --- Prepare global settings ---
    if 'global_settings' in config:
        config.update(config.get('global_settings', {}))
    
    if 'outputs' in config and 'state_dir' in config['outputs']:
        state_dir = config['outputs']['state_dir']
        config['outputs']['db_path'] = os.path.join(state_dir, config['outputs'].get('db_name', 'pipeline.db'))

    # --- Inject GitHub tokens from environment ---
    tokens = _load_env_tokens()
    config.setdefault('github', {})
    config['github']['tokens'] = tokens

    return config

CONFIG = load_and_prepare_config()