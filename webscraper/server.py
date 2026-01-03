"""
Web GUI server for the scraper.
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from .crawler import get_page_links, WebCrawler
from .scraper import ScraperManager

logger = logging.getLogger(__name__)

# Global state
manager = ScraperManager()
sse_clients = []  # Server-Sent Events clients
sse_lock = threading.Lock()


def broadcast_event(data):
    """Send event to all SSE clients."""
    message = f"data: {json.dumps(data)}\n\n"
    with sse_lock:
        dead_clients = []
        for client in sse_clients:
            try:
                client['wfile'].write(message.encode())
                client['wfile'].flush()
            except Exception:
                dead_clients.append(client)
        for client in dead_clients:
            sse_clients.remove(client)


class ScraperHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the scraper GUI."""

    def log_message(self, format, *args):
        logger.info(format % args)

    def send_json(self, data, status=200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            self.serve_html()
        elif path == '/api/events':
            self.handle_sse()
        elif path == '/api/jobs':
            self.send_json(manager.list_jobs())
        elif path.startswith('/api/job/'):
            job_id = path.split('/')[-1]
            job = manager.get_job(job_id)
            if job:
                self.send_json(job.to_dict())
            else:
                self.send_json({'error': 'Job not found'}, 404)
        else:
            self.send_error(404)

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length else '{}'

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json({'error': 'Invalid JSON'}, 400)
            return

        if path == '/api/crawl':
            self.handle_crawl(data)
        elif path == '/api/crawl-deep':
            self.handle_deep_crawl(data)
        elif path == '/api/start':
            self.handle_start(data)
        elif path == '/api/pause':
            self.handle_pause(data)
        else:
            self.send_json({'error': 'Not found'}, 404)

    def serve_html(self):
        """Serve the main HTML page."""
        html = get_html_template()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def handle_sse(self):
        """Handle Server-Sent Events for live updates."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        client = {'wfile': self.wfile}
        with sse_lock:
            sse_clients.append(client)

        # Keep connection open
        try:
            while True:
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                threading.Event().wait(30)
        except Exception:
            pass
        finally:
            with sse_lock:
                if client in sse_clients:
                    sse_clients.remove(client)

    def handle_crawl(self, data):
        """Handle single page link extraction."""
        url = data.get('url', '').strip()
        if not url:
            self.send_json({'error': 'URL required'}, 400)
            return

        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        result = get_page_links(url)
        self.send_json(result)

    def handle_deep_crawl(self, data):
        """Handle deep site crawl."""
        url = data.get('url', '').strip()
        max_pages = min(data.get('max_pages', 50), 200)
        max_depth = min(data.get('max_depth', 2), 5)

        if not url:
            self.send_json({'error': 'URL required'}, 400)
            return

        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        crawler = WebCrawler()

        def on_progress(info):
            broadcast_event({'type': 'crawl_progress', **info})

        # Run in background
        def do_crawl():
            result = crawler.crawl_site(
                url,
                max_pages=max_pages,
                max_depth=max_depth,
                delay=0.5,
                callback=on_progress
            )
            broadcast_event({'type': 'crawl_complete', **result})

        thread = threading.Thread(target=do_crawl)
        thread.daemon = True
        thread.start()

        self.send_json({'status': 'started', 'url': url})

    def handle_start(self, data):
        """Start a scraping job."""
        urls = data.get('urls', [])
        output_dir = data.get('output_dir', '/tmp/scraped')
        options = {
            'delay': data.get('delay', 3),
            'format': data.get('format', 'markdown'),
            'download_images': data.get('download_images', False),
        }

        if not urls:
            self.send_json({'error': 'No URLs provided'}, 400)
            return

        job = manager.create_job(urls, output_dir, options)
        manager.start_job(job.job_id, callback=broadcast_event)

        self.send_json({
            'status': 'started',
            'job_id': job.job_id,
            'total': len(urls),
        })

    def handle_pause(self, data):
        """Pause a running job."""
        job_id = data.get('job_id')
        if job_id and manager.pause_job(job_id):
            self.send_json({'status': 'paused', 'job_id': job_id})
        else:
            self.send_json({'error': 'Job not found'}, 404)


def get_html_template():
    """Return the HTML template for the GUI."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web Scraper</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { margin-bottom: 20px; color: #4fc3f7; }
        h2 { margin: 20px 0 10px; color: #81d4fa; font-size: 1.2em; }

        .card {
            background: #16213e;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #0f3460;
        }

        input[type="text"], input[type="number"], select {
            width: 100%;
            padding: 12px;
            border: 1px solid #0f3460;
            border-radius: 6px;
            background: #1a1a2e;
            color: #eee;
            font-size: 16px;
            margin-bottom: 10px;
        }
        input:focus, select:focus {
            outline: none;
            border-color: #4fc3f7;
        }

        button {
            padding: 12px 24px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.2s;
            margin-right: 10px;
            margin-bottom: 10px;
        }
        .btn-primary { background: #4fc3f7; color: #1a1a2e; }
        .btn-primary:hover { background: #81d4fa; }
        .btn-secondary { background: #0f3460; color: #eee; }
        .btn-secondary:hover { background: #1a4a7a; }
        .btn-danger { background: #e74c3c; color: #fff; }
        .btn-danger:hover { background: #c0392b; }
        .btn-success { background: #27ae60; color: #fff; }
        .btn-success:hover { background: #2ecc71; }

        .url-list {
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid #0f3460;
            border-radius: 6px;
            background: #1a1a2e;
        }
        .url-item {
            display: flex;
            align-items: center;
            padding: 10px 15px;
            border-bottom: 1px solid #0f3460;
            transition: background 0.2s;
        }
        .url-item:hover { background: #0f3460; }
        .url-item:last-child { border-bottom: none; }
        .url-item input[type="checkbox"] {
            width: 18px;
            height: 18px;
            margin-right: 15px;
            cursor: pointer;
        }
        .url-item .url-text {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 14px;
        }
        .url-item .url-title {
            color: #4fc3f7;
            font-weight: 500;
        }
        .url-item .url-path { color: #888; margin-left: 10px; }
        .url-item .delete-btn {
            background: none;
            border: none;
            color: #e74c3c;
            cursor: pointer;
            padding: 5px;
            opacity: 0.7;
        }
        .url-item .delete-btn:hover { opacity: 1; }

        .options-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .option-group label {
            display: block;
            margin-bottom: 5px;
            color: #81d4fa;
            font-size: 14px;
        }

        .progress-bar {
            height: 24px;
            background: #0f3460;
            border-radius: 12px;
            overflow: hidden;
            margin: 15px 0;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #4fc3f7, #27ae60);
            transition: width 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #1a1a2e;
            font-weight: 600;
            font-size: 12px;
        }

        .log {
            background: #0a0a14;
            border-radius: 6px;
            padding: 15px;
            max-height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 13px;
            line-height: 1.6;
        }
        .log-entry { margin-bottom: 5px; }
        .log-success { color: #27ae60; }
        .log-error { color: #e74c3c; }
        .log-info { color: #4fc3f7; }

        .stats {
            display: flex;
            gap: 20px;
            margin: 15px 0;
        }
        .stat {
            background: #0f3460;
            padding: 15px 20px;
            border-radius: 6px;
            text-align: center;
        }
        .stat-value { font-size: 24px; font-weight: 700; color: #4fc3f7; }
        .stat-label { font-size: 12px; color: #888; margin-top: 5px; }

        .checkbox-label {
            display: flex;
            align-items: center;
            cursor: pointer;
        }
        .checkbox-label input { margin-right: 8px; }

        .actions { margin-top: 15px; }
        .flex-row { display: flex; gap: 10px; align-items: center; }

        #manual-urls {
            width: 100%;
            height: 120px;
            padding: 12px;
            border: 1px solid #0f3460;
            border-radius: 6px;
            background: #1a1a2e;
            color: #eee;
            font-family: monospace;
            font-size: 14px;
            resize: vertical;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌐 Web Scraper</h1>

        <!-- Step 1: Crawl -->
        <div class="card">
            <h2>1. Find Links</h2>
            <p style="color: #888; margin-bottom: 15px;">Enter a website URL to find all links, or add URLs manually below.</p>
            <div class="flex-row">
                <input type="text" id="crawl-url" placeholder="https://example.com" style="flex:1; margin-bottom:0;">
                <button class="btn-primary" onclick="crawlPage()">Get Links</button>
                <button class="btn-secondary" onclick="deepCrawl()">Deep Crawl</button>
            </div>
            <div id="crawl-status" style="margin-top: 10px; color: #888;"></div>
        </div>

        <!-- URL List -->
        <div class="card">
            <h2>2. Select URLs to Scrape</h2>
            <div style="margin-bottom: 15px;">
                <button class="btn-secondary" onclick="selectAll()">Select All</button>
                <button class="btn-secondary" onclick="selectNone()">Select None</button>
                <button class="btn-danger" onclick="deleteSelected()">Delete Selected</button>
                <span id="selected-count" style="margin-left: 15px; color: #888;"></span>
            </div>
            <div class="url-list" id="url-list">
                <div style="padding: 40px; text-align: center; color: #666;">
                    No URLs yet. Crawl a website or add URLs manually below.
                </div>
            </div>

            <h2 style="margin-top: 20px;">Or Add URLs Manually</h2>
            <textarea id="manual-urls" placeholder="Paste URLs here, one per line..."></textarea>
            <button class="btn-secondary" onclick="addManualUrls()" style="margin-top: 10px;">Add URLs</button>
        </div>

        <!-- Options -->
        <div class="card">
            <h2>3. Options</h2>
            <div class="options-grid">
                <div class="option-group">
                    <label>Output Format</label>
                    <select id="format">
                        <option value="markdown">Markdown (.md)</option>
                        <option value="html">HTML (.html)</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Delay Between Requests (seconds)</label>
                    <input type="number" id="delay" value="3" min="1" max="60">
                </div>
                <div class="option-group">
                    <label>Output Folder</label>
                    <input type="text" id="output-dir" value="/volume1/scraped" placeholder="/volume1/scraped">
                </div>
                <div class="option-group">
                    <label>&nbsp;</label>
                    <label class="checkbox-label">
                        <input type="checkbox" id="download-images">
                        Download Images
                    </label>
                </div>
            </div>
        </div>

        <!-- Start/Progress -->
        <div class="card">
            <h2>4. Scrape</h2>
            <div class="actions">
                <button class="btn-success" onclick="startScraping()" id="start-btn">Start Scraping</button>
                <button class="btn-danger" onclick="pauseScraping()" id="pause-btn" style="display:none;">Pause</button>
            </div>

            <div id="progress-section" style="display: none;">
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value" id="stat-completed">0</div>
                        <div class="stat-label">Completed</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="stat-failed">0</div>
                        <div class="stat-label">Failed</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="stat-remaining">0</div>
                        <div class="stat-label">Remaining</div>
                    </div>
                </div>

                <div class="progress-bar">
                    <div class="progress-fill" id="progress-fill" style="width: 0%;">0%</div>
                </div>

                <div class="log" id="log"></div>
            </div>
        </div>
    </div>

    <script>
        let urls = [];
        let currentJobId = null;

        // SSE for live updates
        const evtSource = new EventSource('/api/events');
        evtSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            handleEvent(data);
        };

        function handleEvent(data) {
            if (data.type === 'progress') {
                updateProgress(data);
            } else if (data.type === 'result') {
                addLogEntry(data);
                updateStats(data);
            } else if (data.type === 'complete') {
                jobComplete(data);
            } else if (data.type === 'crawl_progress') {
                document.getElementById('crawl-status').textContent =
                    `Crawling... Found ${data.pages_found} pages, ${data.queue_size} in queue`;
            } else if (data.type === 'crawl_complete') {
                document.getElementById('crawl-status').textContent =
                    `Done! Found ${data.pages.length} pages`;
                data.pages.forEach(p => addUrl(p.url, p.title));
                updateUrlList();
            }
        }

        function crawlPage() {
            const url = document.getElementById('crawl-url').value.trim();
            if (!url) return alert('Please enter a URL');

            document.getElementById('crawl-status').textContent = 'Fetching links...';

            fetch('/api/crawl', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('crawl-status').textContent = 'Error: ' + data.error;
                    return;
                }
                document.getElementById('crawl-status').textContent =
                    `Found ${data.internal.length} internal + ${data.external.length} external links`;

                // Add internal links
                data.internal.forEach(u => addUrl(u));
                updateUrlList();
            })
            .catch(err => {
                document.getElementById('crawl-status').textContent = 'Error: ' + err.message;
            });
        }

        function deepCrawl() {
            const url = document.getElementById('crawl-url').value.trim();
            if (!url) return alert('Please enter a URL');

            document.getElementById('crawl-status').textContent = 'Starting deep crawl...';

            fetch('/api/crawl-deep', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, max_pages: 50, max_depth: 2})
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('crawl-status').textContent = 'Error: ' + data.error;
                }
            });
        }

        function addUrl(url, title) {
            if (!urls.find(u => u.url === url)) {
                urls.push({url, title: title || '', selected: true});
            }
        }

        function updateUrlList() {
            const container = document.getElementById('url-list');
            if (urls.length === 0) {
                container.innerHTML = '<div style="padding: 40px; text-align: center; color: #666;">No URLs yet.</div>';
                return;
            }

            container.innerHTML = urls.map((u, i) => `
                <div class="url-item">
                    <input type="checkbox" ${u.selected ? 'checked' : ''} onchange="toggleUrl(${i})">
                    <div class="url-text">
                        ${u.title ? `<span class="url-title">${escapeHtml(u.title)}</span>` : ''}
                        <span class="url-path">${escapeHtml(u.url)}</span>
                    </div>
                    <button class="delete-btn" onclick="deleteUrl(${i})">✕</button>
                </div>
            `).join('');

            updateSelectedCount();
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function toggleUrl(index) {
            urls[index].selected = !urls[index].selected;
            updateSelectedCount();
        }

        function updateSelectedCount() {
            const count = urls.filter(u => u.selected).length;
            document.getElementById('selected-count').textContent = `${count} of ${urls.length} selected`;
        }

        function selectAll() {
            urls.forEach(u => u.selected = true);
            updateUrlList();
        }

        function selectNone() {
            urls.forEach(u => u.selected = false);
            updateUrlList();
        }

        function deleteSelected() {
            urls = urls.filter(u => !u.selected);
            updateUrlList();
        }

        function deleteUrl(index) {
            urls.splice(index, 1);
            updateUrlList();
        }

        function addManualUrls() {
            const text = document.getElementById('manual-urls').value;
            const newUrls = text.split('\\n')
                .map(u => u.trim())
                .filter(u => u && (u.startsWith('http://') || u.startsWith('https://')));

            newUrls.forEach(u => addUrl(u));
            document.getElementById('manual-urls').value = '';
            updateUrlList();
        }

        function startScraping() {
            const selectedUrls = urls.filter(u => u.selected).map(u => u.url);
            if (selectedUrls.length === 0) {
                return alert('Please select at least one URL');
            }

            const options = {
                urls: selectedUrls,
                output_dir: document.getElementById('output-dir').value,
                format: document.getElementById('format').value,
                delay: parseInt(document.getElementById('delay').value) || 3,
                download_images: document.getElementById('download-images').checked
            };

            document.getElementById('progress-section').style.display = 'block';
            document.getElementById('start-btn').style.display = 'none';
            document.getElementById('pause-btn').style.display = 'inline-block';
            document.getElementById('log').innerHTML = '';

            fetch('/api/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(options)
            })
            .then(r => r.json())
            .then(data => {
                currentJobId = data.job_id;
                addLogEntry({status: 'info', message: `Started job ${data.job_id} with ${data.total} URLs`});
            });
        }

        function pauseScraping() {
            if (currentJobId) {
                fetch('/api/pause', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({job_id: currentJobId})
                });
            }
        }

        function updateProgress(data) {
            const pct = Math.round((data.current / data.total) * 100);
            document.getElementById('progress-fill').style.width = pct + '%';
            document.getElementById('progress-fill').textContent = `${data.current}/${data.total} (${pct}%)`;
        }

        function updateStats(data) {
            // This is called per-result, we'd need cumulative stats
        }

        function addLogEntry(data) {
            const log = document.getElementById('log');
            const cls = data.status === 'success' ? 'log-success' :
                       data.status === 'error' ? 'log-error' : 'log-info';
            const msg = data.message || (data.status === 'success' ?
                `✓ ${data.file || data.url}` : `✗ ${data.url}: ${data.error}`);
            log.innerHTML += `<div class="log-entry ${cls}">${escapeHtml(msg)}</div>`;
            log.scrollTop = log.scrollHeight;
        }

        function jobComplete(data) {
            document.getElementById('stat-completed').textContent = data.completed;
            document.getElementById('stat-failed').textContent = data.failed;
            document.getElementById('stat-remaining').textContent = 0;

            document.getElementById('start-btn').style.display = 'inline-block';
            document.getElementById('start-btn').textContent = 'Start New Job';
            document.getElementById('pause-btn').style.display = 'none';

            addLogEntry({status: 'info', message: `Job complete! ${data.completed} succeeded, ${data.failed} failed`});
        }
    </script>
</body>
</html>'''


def run_server(host='0.0.0.0', port=5126):
    """Run the web scraper server."""
    server = HTTPServer((host, port), ScraperHandler)
    print(f"Web Scraper running at http://{host}:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_server()
