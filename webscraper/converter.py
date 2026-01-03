"""
HTML to Markdown converter using only Python standard library.
"""

import re
from html.parser import HTMLParser


class HTMLToMarkdown(HTMLParser):
    """Convert HTML to Markdown format."""

    def __init__(self, include_images=True, base_url=''):
        super().__init__()
        self.include_images = include_images
        self.base_url = base_url.rstrip('/')
        self.result = []
        self.tag_stack = []
        self.list_stack = []  # Track nested lists: 'ul' or 'ol'
        self.list_counters = []  # For ordered lists
        self.in_pre = False
        self.in_code = False
        self.skip_content = False
        self.link_href = ''
        self.images = []  # Collect image URLs

    def _get_indent(self):
        """Get indentation for nested lists."""
        return '  ' * max(0, len(self.list_stack) - 1)

    def _resolve_url(self, url):
        """Resolve relative URLs to absolute."""
        if not url:
            return ''
        if url.startswith(('http://', 'https://', '//')):
            return url
        if url.startswith('/'):
            # Parse base_url to get scheme + host
            if self.base_url:
                parts = self.base_url.split('/')
                if len(parts) >= 3:
                    return f"{parts[0]}//{parts[2]}{url}"
            return url
        return f"{self.base_url}/{url}" if self.base_url else url

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self.tag_stack.append(tag)

        # Skip script, style, nav, footer, header content
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'):
            self.skip_content = True
            return

        if self.skip_content:
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag[1])
            self.result.append('\n\n' + '#' * level + ' ')
        elif tag == 'p':
            self.result.append('\n\n')
        elif tag == 'br':
            self.result.append('\n')
        elif tag == 'hr':
            self.result.append('\n\n---\n\n')
        elif tag == 'strong' or tag == 'b':
            self.result.append('**')
        elif tag == 'em' or tag == 'i':
            self.result.append('*')
        elif tag == 'code':
            if not self.in_pre:
                self.result.append('`')
            self.in_code = True
        elif tag == 'pre':
            self.result.append('\n\n```\n')
            self.in_pre = True
        elif tag == 'blockquote':
            self.result.append('\n\n> ')
        elif tag == 'a':
            self.link_href = self._resolve_url(attrs_dict.get('href', ''))
            self.result.append('[')
        elif tag == 'img' and self.include_images:
            src = self._resolve_url(attrs_dict.get('src', ''))
            alt = attrs_dict.get('alt', 'image')
            if src:
                self.images.append(src)
                self.result.append(f'\n\n![{alt}]({src})\n\n')
        elif tag == 'ul':
            self.list_stack.append('ul')
            self.result.append('\n')
        elif tag == 'ol':
            self.list_stack.append('ol')
            self.list_counters.append(0)
            self.result.append('\n')
        elif tag == 'li':
            indent = self._get_indent()
            if self.list_stack:
                if self.list_stack[-1] == 'ol':
                    self.list_counters[-1] += 1
                    self.result.append(f'\n{indent}{self.list_counters[-1]}. ')
                else:
                    self.result.append(f'\n{indent}- ')
            else:
                self.result.append('\n- ')
        elif tag == 'table':
            self.result.append('\n\n')
        elif tag == 'tr':
            self.result.append('|')
        elif tag == 'th' or tag == 'td':
            self.result.append(' ')
        elif tag == 'div':
            self.result.append('\n')

    def handle_endtag(self, tag):
        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()

        # Check if we're exiting a skip zone
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'):
            self.skip_content = False
            return

        if self.skip_content:
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.result.append('\n')
        elif tag == 'p':
            self.result.append('\n')
        elif tag == 'strong' or tag == 'b':
            self.result.append('**')
        elif tag == 'em' or tag == 'i':
            self.result.append('*')
        elif tag == 'code':
            if not self.in_pre:
                self.result.append('`')
            self.in_code = False
        elif tag == 'pre':
            self.result.append('\n```\n\n')
            self.in_pre = False
        elif tag == 'a':
            self.result.append(f']({self.link_href})')
            self.link_href = ''
        elif tag == 'ul':
            if self.list_stack and self.list_stack[-1] == 'ul':
                self.list_stack.pop()
            self.result.append('\n')
        elif tag == 'ol':
            if self.list_stack and self.list_stack[-1] == 'ol':
                self.list_stack.pop()
                if self.list_counters:
                    self.list_counters.pop()
            self.result.append('\n')
        elif tag == 'tr':
            self.result.append('\n')
        elif tag == 'th':
            self.result.append(' |')
        elif tag == 'td':
            self.result.append(' |')
        elif tag == 'thead':
            # Add markdown table separator after header
            self.result.append('|---|---|\n')
        elif tag == 'blockquote':
            self.result.append('\n')

    def handle_data(self, data):
        if self.skip_content:
            return

        if self.in_pre:
            self.result.append(data)
        else:
            # Normalize whitespace for non-pre content
            text = re.sub(r'\s+', ' ', data)
            if text.strip():
                self.result.append(text)

    def handle_entityref(self, name):
        if self.skip_content:
            return
        entities = {
            'nbsp': ' ', 'lt': '<', 'gt': '>', 'amp': '&',
            'quot': '"', 'apos': "'", 'mdash': '—', 'ndash': '–',
            'copy': '©', 'reg': '®', 'trade': '™',
        }
        self.result.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        if self.skip_content:
            return
        try:
            if name.startswith('x'):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self.result.append(char)
        except ValueError:
            self.result.append(f'&#{name};')

    def get_markdown(self):
        """Get the converted markdown, cleaned up."""
        text = ''.join(self.result)
        # Clean up excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Clean up spaces before punctuation
        text = re.sub(r' +([.,!?;:])', r'\1', text)
        # Clean up multiple spaces
        text = re.sub(r' +', ' ', text)
        return text.strip()

    def get_images(self):
        """Get list of image URLs found."""
        return self.images


def html_to_markdown(html_content, include_images=True, base_url=''):
    """
    Convert HTML content to Markdown.

    Args:
        html_content: The HTML string to convert
        include_images: Whether to include image references
        base_url: Base URL for resolving relative links

    Returns:
        tuple: (markdown_text, list_of_image_urls)
    """
    parser = HTMLToMarkdown(include_images=include_images, base_url=base_url)
    try:
        parser.feed(html_content)
    except Exception:
        pass  # Best effort parsing
    return parser.get_markdown(), parser.get_images()


def extract_title(html_content):
    """Extract the title from HTML content."""
    match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Try h1
    match = re.search(r'<h1[^>]*>([^<]+)</h1>', html_content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return 'Untitled'
