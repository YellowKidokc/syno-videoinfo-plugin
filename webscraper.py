#!/usr/bin/env python3
"""
Web Scraper - A simple web scraper with GUI.

Usage:
    python3 webscraper.py              # Start the web GUI (default)
    python3 webscraper.py --gui        # Start the web GUI
    python3 webscraper.py --cli        # Command-line mode (batch processing)

GUI mode opens a web interface at http://localhost:5126

CLI Examples:
    python3 webscraper.py --cli --urls urls.txt --output /volume1/scraped
    python3 webscraper.py --cli --url https://example.com --output ./output
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from webscraper.server import run_server
from webscraper.scraper import ScraperJob


def cli_mode(args):
    """Run in CLI mode for batch processing."""
    urls = []

    # Get URLs from file
    if args.urls:
        with open(args.urls) as f:
            urls.extend(line.strip() for line in f if line.strip())

    # Get single URL
    if args.url:
        urls.append(args.url)

    if not urls:
        print("Error: No URLs provided. Use --url or --urls")
        sys.exit(1)

    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scraping {len(urls)} URLs to {output_dir}")
    print(f"Format: {args.format}, Delay: {args.delay}s, Images: {args.images}")
    print("-" * 50)

    options = {
        'delay': args.delay,
        'format': args.format,
        'download_images': args.images,
    }

    job = ScraperJob('cli', urls, str(output_dir), options)

    def on_progress(data):
        if data.get('type') == 'progress':
            print(f"[{data['current']}/{data['total']}] {data['url']}")
        elif data.get('type') == 'result':
            status = '✓' if data['status'] == 'success' else '✗'
            msg = data.get('file') or data.get('error', 'Unknown error')
            print(f"  {status} {msg}")
        elif data.get('type') == 'complete':
            print("-" * 50)
            print(f"Complete! {data['completed']} succeeded, {data['failed']} failed")

    job.run(callback=on_progress)


def gui_mode(args):
    """Run in GUI mode."""
    print(f"Starting Web Scraper GUI on port {args.port}...")
    print(f"Open http://localhost:{args.port} in your browser")
    print()
    run_server(host='0.0.0.0', port=args.port)


def main():
    parser = argparse.ArgumentParser(
        description='Web Scraper - Download websites as Markdown or HTML',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--gui', action='store_true', default=True,
                       help='Run in GUI mode (default)')
    parser.add_argument('--cli', action='store_true',
                       help='Run in CLI mode for batch processing')
    parser.add_argument('--port', type=int, default=5126,
                       help='Port for GUI server (default: 5126)')

    # CLI mode options
    parser.add_argument('--url', help='Single URL to scrape')
    parser.add_argument('--urls', help='File containing URLs (one per line)')
    parser.add_argument('--output', '-o', default='./scraped',
                       help='Output directory (default: ./scraped)')
    parser.add_argument('--format', '-f', choices=['markdown', 'html'],
                       default='markdown', help='Output format (default: markdown)')
    parser.add_argument('--delay', '-d', type=int, default=3,
                       help='Delay between requests in seconds (default: 3)')
    parser.add_argument('--images', '-i', action='store_true',
                       help='Download images')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    if args.cli:
        cli_mode(args)
    else:
        gui_mode(args)


if __name__ == '__main__':
    main()
