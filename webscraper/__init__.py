"""
Web Scraper - A simple web scraper with GUI for Synology NAS.
"""

from .converter import html_to_markdown, extract_title
from .crawler import WebCrawler, get_page_links
from .scraper import ScraperJob, ScraperManager
from .server import run_server

__all__ = [
    'html_to_markdown',
    'extract_title',
    'WebCrawler',
    'get_page_links',
    'ScraperJob',
    'ScraperManager',
    'run_server',
]
