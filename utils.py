# developer_finder/utils.py
import os
import json
import logging
from typing import Optional, List, Any, Dict
import re

logger = logging.getLogger(__name__)

# Regex patterns for finding contact info
EMAIL_REGEX = re.compile(r"[a-z0-9\.\-+_]+@[a-z0-9\.\-+_]+\.[a-z]+")
LINKEDIN_REGEX = re.compile(r"linkedin\.com/in/([a-zA-Z0-9_-]+)")

def extract_contact_info(text: Optional[str]) -> Dict[str, Optional[str]]:
    """
    Parses a block of text (like a bio or README) to find an email
    and a LinkedIn profile URL using regular expressions.
    """
    if not text:
        return {"email": None, "linkedin_url": None}

    email_match = EMAIL_REGEX.search(text.lower())
    linkedin_match = LINKEDIN_REGEX.search(text.lower())

    email = email_match.group(0) if email_match else None
    linkedin_url = f"https://www.linkedin.com/in/{linkedin_match.group(1)}" if linkedin_match else None
    
    return {"email": email, "linkedin_url": linkedin_url}

def ensure_dirs(config: dict):
    """Create all necessary output directories based on the config."""
    try:
        os.makedirs(config['outputs']['state_dir'], exist_ok=True)
        os.makedirs(config['outputs']['diffs_dir'], exist_ok=True)
    except OSError as e:
        logger.error(f"Error creating directories: {e}")
        raise

def normalize(text: Optional[str]) -> str:
    """Lowercase and remove common punctuation for matching."""
    if not text:
        return ""
    return text.lower().replace(".", " ").replace(",", " ").replace("_", " ").replace("-", " ")

def text_matches_keywords(text: Optional[str], keywords: List[str]) -> bool:
    """Efficiently check if any keyword exists in the text."""
    if not text:
        return False
    normalized_text = normalize(text)
    return any(f" {kw} " in f" {normalized_text} " for kw in keywords)