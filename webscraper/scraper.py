"""
Web scraper backend - downloads pages and converts to markdown/HTML.
"""

import os
import re
import json
import time
import logging
import hashlib
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from urllib.error import URLError, HTTPError
from http.cookiejar import CookieJar

from .converter import html_to_markdown, extract_title

logger = logging.getLogger(__name__)


class ScraperJob:
    """Represents a scraping job with progress tracking."""

    def __init__(self, job_id, urls, output_dir, options=None):
        self.job_id = job_id
        self.urls = urls  # List of URLs to scrape
        self.output_dir = Path(output_dir)
        self.options = options or {}

        # Options with defaults
        self.delay = self.options.get('delay', 3)  # Seconds between requests
        self.format = self.options.get('format', 'markdown')  # 'markdown' or 'html'
        self.download_images = self.options.get('download_images', False)
        self.user_agent = self.options.get('user_agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

        # State
        self.status = 'pending'  # pending, running, paused, completed, failed
        self.current_index = 0
        self.results = []  # List of {url, status, file, error}
        self.start_time = None
        self.end_time = None
        self.lock = threading.Lock()

        # HTTP client
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))

    def to_dict(self):
        """Serialize job state."""
        with self.lock:
            return {
                'job_id': self.job_id,
                'status': self.status,
                'total': len(self.urls),
                'current': self.current_index,
                'completed': len([r for r in self.results if r['status'] == 'success']),
                'failed': len([r for r in self.results if r['status'] == 'error']),
                'results': self.results[-20:],  # Last 20 results
                'start_time': self.start_time,
                'end_time': self.end_time,
                'options': self.options,
            }

    def save_state(self):
        """Save job state to disk for resume capability."""
        state_file = self.output_dir / f'.job_{self.job_id}.json'
        with open(state_file, 'w') as f:
            json.dump({
                'job_id': self.job_id,
                'urls': self.urls,
                'current_index': self.current_index,
                'results': self.results,
                'options': self.options,
                'status': self.status,
            }, f)

    @classmethod
    def load_state(cls, state_file):
        """Load job state from disk."""
        with open(state_file) as f:
            data = json.load(f)
        job = cls(
            data['job_id'],
            data['urls'],
            str(state_file.parent),
            data['options']
        )
        job.current_index = data['current_index']
        job.results = data['results']
        job.status = 'paused'  # Mark as paused, not original status
        return job

    def _sanitize_filename(self, url, title):
        """Create a safe filename from URL and title."""
        parsed = urlparse(url)
        # Use title if available, otherwise path
        if title and title != 'Untitled':
            name = title
        else:
            name = parsed.path.strip('/').replace('/', '_') or 'index'

        # Sanitize
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'\s+', '_', name)
        name = name[:100]  # Limit length

        # Add hash for uniqueness
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

        ext = '.md' if self.format == 'markdown' else '.html'
        return f"{name}_{url_hash}{ext}"

    def _fetch_url(self, url):
        """Fetch a URL and return content."""
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'identity',
            'Connection': 'keep-alive',
        }
        request = Request(url, headers=headers)
        response = self.opener.open(request, timeout=20)

        content_type = response.headers.get('Content-Type', '')
        charset = 'utf-8'
        if 'charset=' in content_type:
            charset = content_type.split('charset=')[-1].split(';')[0].strip()

        content = response.read()
        try:
            return content.decode(charset)
        except UnicodeDecodeError:
            return content.decode('utf-8', errors='replace')

    def _download_image(self, img_url, images_dir):
        """Download an image and return local path."""
        try:
            parsed = urlparse(img_url)
            ext = os.path.splitext(parsed.path)[1] or '.jpg'
            img_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]
            filename = f"{img_hash}{ext}"
            filepath = images_dir / filename

            if filepath.exists():
                return str(filepath.relative_to(self.output_dir))

            headers = {'User-Agent': self.user_agent}
            request = Request(img_url, headers=headers)
            response = self.opener.open(request, timeout=15)
            content = response.read()

            with open(filepath, 'wb') as f:
                f.write(content)

            return str(filepath.relative_to(self.output_dir))
        except Exception as e:
            logger.warning(f"Failed to download image {img_url}: {e}")
            return None

    def _process_url(self, url):
        """Process a single URL."""
        try:
            html_content = self._fetch_url(url)
            title = extract_title(html_content)

            # Convert to markdown
            markdown_content, images = html_to_markdown(
                html_content,
                include_images=True,
                base_url=url
            )

            # Check if markdown conversion looks reasonable
            # If it's too short or mostly empty, fall back to HTML
            use_markdown = self.format == 'markdown'
            if use_markdown and len(markdown_content.strip()) < 100:
                # Markdown too short, might have failed - use HTML
                use_markdown = False
                logger.info(f"Markdown too short for {url}, falling back to HTML")

            filename = self._sanitize_filename(url, title)

            # Handle image downloading
            if self.download_images and images:
                images_dir = self.output_dir / 'images'
                images_dir.mkdir(exist_ok=True)
                for img_url in images[:50]:  # Limit to 50 images per page
                    local_path = self._download_image(img_url, images_dir)
                    if local_path and use_markdown:
                        # Update markdown with local path
                        markdown_content = markdown_content.replace(
                            f']({img_url})',
                            f']({local_path})'
                        )
                    time.sleep(0.5)  # Brief delay between images

            # Prepare content to save
            if use_markdown:
                # Add metadata header
                content = f"""---
title: {title}
url: {url}
scraped: {datetime.now().isoformat()}
---

# {title}

{markdown_content}
"""
                filename = filename.replace('.html', '.md')
            else:
                # Save original HTML with metadata comment
                content = f"""<!--
title: {title}
url: {url}
scraped: {datetime.now().isoformat()}
-->
{html_content}
"""
                filename = filename.replace('.md', '.html')

            # Save file
            filepath = self.output_dir / filename
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            return {
                'url': url,
                'status': 'success',
                'file': filename,
                'title': title,
                'error': None,
            }

        except (URLError, HTTPError) as e:
            return {
                'url': url,
                'status': 'error',
                'file': None,
                'title': None,
                'error': str(e),
            }
        except Exception as e:
            logger.exception(f"Error processing {url}")
            return {
                'url': url,
                'status': 'error',
                'file': None,
                'title': None,
                'error': str(e),
            }

    def run(self, callback=None):
        """
        Run the scraping job.

        Args:
            callback: Function called with progress updates
        """
        self.status = 'running'
        self.start_time = datetime.now().isoformat()

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            while self.current_index < len(self.urls):
                if self.status == 'paused':
                    self.save_state()
                    break

                url = self.urls[self.current_index]

                if callback:
                    callback({
                        'type': 'progress',
                        'job_id': self.job_id,
                        'current': self.current_index + 1,
                        'total': len(self.urls),
                        'url': url,
                    })

                result = self._process_url(url)

                with self.lock:
                    self.results.append(result)
                    self.current_index += 1

                if callback:
                    callback({
                        'type': 'result',
                        'job_id': self.job_id,
                        **result,
                    })

                # Save state periodically
                if self.current_index % 10 == 0:
                    self.save_state()

                # Delay between requests
                if self.current_index < len(self.urls) and self.delay > 0:
                    time.sleep(self.delay)

            if self.status == 'running':
                self.status = 'completed'
                self.end_time = datetime.now().isoformat()

        except Exception as e:
            logger.exception("Job failed")
            self.status = 'failed'
            self.end_time = datetime.now().isoformat()

        self.save_state()

        if callback:
            callback({
                'type': 'complete',
                'job_id': self.job_id,
                'status': self.status,
                'total': len(self.urls),
                'completed': len([r for r in self.results if r['status'] == 'success']),
                'failed': len([r for r in self.results if r['status'] == 'error']),
            })

    def pause(self):
        """Pause the job."""
        self.status = 'paused'

    def resume(self, callback=None):
        """Resume a paused job."""
        if self.status == 'paused':
            self.run(callback)


class ScraperManager:
    """Manages multiple scraping jobs."""

    def __init__(self):
        self.jobs = {}  # job_id -> ScraperJob
        self.lock = threading.Lock()

    def create_job(self, urls, output_dir, options=None):
        """Create a new scraping job."""
        job_id = hashlib.md5(
            f"{time.time()}{urls[0] if urls else ''}".encode()
        ).hexdigest()[:12]

        job = ScraperJob(job_id, urls, output_dir, options)

        with self.lock:
            self.jobs[job_id] = job

        return job

    def get_job(self, job_id):
        """Get a job by ID."""
        return self.jobs.get(job_id)

    def start_job(self, job_id, callback=None):
        """Start a job in a background thread."""
        job = self.jobs.get(job_id)
        if job:
            thread = threading.Thread(target=job.run, args=(callback,))
            thread.daemon = True
            thread.start()
            return True
        return False

    def pause_job(self, job_id):
        """Pause a running job."""
        job = self.jobs.get(job_id)
        if job:
            job.pause()
            return True
        return False

    def list_jobs(self):
        """List all jobs."""
        return [job.to_dict() for job in self.jobs.values()]
