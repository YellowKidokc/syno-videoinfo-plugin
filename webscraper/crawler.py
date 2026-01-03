"""
Link crawler - finds all links on a website.
"""

import re
import time
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor

logger = logging.getLogger(__name__)


class LinkExtractor(HTMLParser):
    """Extract all links from HTML content."""

    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc
        self.links = set()
        self.internal_links = set()
        self.external_links = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs_dict = dict(attrs)
            href = attrs_dict.get('href', '')
            if href and not href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                full_url = urljoin(self.base_url, href)
                # Normalize the URL
                parsed = urlparse(full_url)
                # Remove fragments
                normalized = urlunparse((
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path.rstrip('/') or '/',
                    parsed.params,
                    parsed.query,
                    ''  # Remove fragment
                ))
                if parsed.scheme in ('http', 'https'):
                    self.links.add(normalized)
                    if parsed.netloc == self.base_domain:
                        self.internal_links.add(normalized)
                    else:
                        self.external_links.add(normalized)


class WebCrawler:
    """Crawl a website and find all links."""

    def __init__(self, user_agent=None, timeout=15):
        self.user_agent = user_agent or 'Mozilla/5.0 (compatible; WebScraper/1.0)'
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.visited = set()
        self.failed = set()

    def fetch(self, url):
        """Fetch a URL and return the content."""
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'identity',
            'Connection': 'keep-alive',
        }
        request = Request(url, headers=headers)
        try:
            response = self.opener.open(request, timeout=self.timeout)
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type and 'text/plain' not in content_type:
                return None, content_type
            charset = 'utf-8'
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1].split(';')[0].strip()
            content = response.read()
            try:
                return content.decode(charset), content_type
            except UnicodeDecodeError:
                return content.decode('utf-8', errors='replace'), content_type
        except (URLError, HTTPError) as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            self.failed.add(url)
            return None, None

    def extract_links(self, url, html_content):
        """Extract all links from HTML content."""
        parser = LinkExtractor(url)
        try:
            parser.feed(html_content)
        except Exception as e:
            logger.warning(f"Error parsing HTML from {url}: {e}")
        return parser.internal_links, parser.external_links

    def crawl_page(self, url):
        """
        Crawl a single page and return its links.

        Returns:
            dict with 'internal', 'external', 'content', 'title'
        """
        if url in self.visited:
            return None

        self.visited.add(url)
        content, content_type = self.fetch(url)

        if content is None:
            return None

        internal, external = self.extract_links(url, content)

        # Extract title
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else urlparse(url).path

        return {
            'url': url,
            'title': title,
            'internal': list(internal),
            'external': list(external),
            'content': content,
            'content_type': content_type,
        }

    def crawl_site(self, start_url, max_pages=100, same_domain_only=True,
                   delay=1.0, callback=None, max_depth=3):
        """
        Crawl a website starting from the given URL.

        Args:
            start_url: Starting URL
            max_pages: Maximum number of pages to crawl
            same_domain_only: Only follow links on the same domain
            delay: Delay between requests in seconds
            callback: Function to call with progress updates
            max_depth: Maximum crawl depth from start URL

        Returns:
            dict with 'pages' (list of page info) and 'all_links' (set of all found links)
        """
        parsed_start = urlparse(start_url)
        base_domain = parsed_start.netloc

        # Queue: (url, depth)
        queue = [(start_url, 0)]
        pages = []
        all_internal_links = set()
        all_external_links = set()

        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)

            if url in self.visited:
                continue

            if callback:
                callback({
                    'status': 'crawling',
                    'current_url': url,
                    'pages_found': len(pages),
                    'queue_size': len(queue),
                })

            result = self.crawl_page(url)

            if result:
                pages.append({
                    'url': result['url'],
                    'title': result['title'],
                    'depth': depth,
                })
                all_internal_links.update(result['internal'])
                all_external_links.update(result['external'])

                # Add internal links to queue if within depth limit
                if depth < max_depth:
                    for link in result['internal']:
                        if link not in self.visited:
                            parsed_link = urlparse(link)
                            if same_domain_only and parsed_link.netloc != base_domain:
                                continue
                            # Skip common non-content URLs
                            skip_patterns = [
                                '/login', '/logout', '/signup', '/register',
                                '/cart', '/checkout', '/admin', '/wp-admin',
                                '.pdf', '.zip', '.exe', '.dmg', '.pkg',
                                '.jpg', '.jpeg', '.png', '.gif', '.svg',
                                '.css', '.js', '.json', '.xml',
                            ]
                            path_lower = parsed_link.path.lower()
                            if not any(p in path_lower for p in skip_patterns):
                                queue.append((link, depth + 1))

            if delay > 0 and queue:
                time.sleep(delay)

        if callback:
            callback({
                'status': 'complete',
                'pages_found': len(pages),
                'total_internal_links': len(all_internal_links),
                'total_external_links': len(all_external_links),
            })

        return {
            'pages': pages,
            'internal_links': list(all_internal_links),
            'external_links': list(all_external_links),
            'failed': list(self.failed),
        }


def get_page_links(url, user_agent=None):
    """
    Quick function to get all links from a single page.

    Returns:
        dict with 'internal', 'external', 'title', 'error'
    """
    crawler = WebCrawler(user_agent=user_agent)
    result = crawler.crawl_page(url)

    if result is None:
        return {
            'error': 'Failed to fetch page',
            'internal': [],
            'external': [],
            'title': '',
        }

    return {
        'url': url,
        'title': result['title'],
        'internal': result['internal'],
        'external': result['external'],
        'error': None,
    }
