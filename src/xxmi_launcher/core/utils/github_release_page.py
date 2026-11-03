"""Read public release metadata when the GitHub API is unavailable."""
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit


class ReleasePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.body = []
        self.body_count = 0
        self.body_depth = 0

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'a' and attributes.get('href'):
            self.links.append(attributes['href'])
        if tag == 'div':
            if self.body_depth:
                self.body_depth += 1
            elif 'markdown-body' in attributes.get('class', '').split():
                self.body_count += 1
                self.body_depth = 1
        if self.body_depth:
            if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                self.body.append('\n' + '#' * int(tag[1]) + ' ')
            elif tag == 'li':
                self.body.append('\n- ')
            elif tag in ('p', 'br'):
                self.body.append('\n')

    def handle_endtag(self, tag):
        if self.body_depth and tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'p'):
            self.body.append('\n')
        if self.body_depth and tag == 'div':
            self.body_depth -= 1

    def handle_data(self, data):
        if self.body_depth:
            self.body.append(data)

    def markdown(self):
        # Keep headings and list items, including Signature and Signature Extra,
        # in the format consumed by the existing signature parser.
        return '\n'.join(line.strip() for line in ''.join(self.body).splitlines() if line.strip())


def fetch_release_page(get, owner, repository):
    base = f'https://github.com/{owner}/{repository}'
    release = get(base + '/releases/latest')
    release.raise_for_status()
    address = urlsplit(release.url)
    prefix = f'/{owner}/{repository}/releases/tag/'
    if address.scheme != 'https' or address.netloc != 'github.com' or not address.path.startswith(prefix) or address.query or address.fragment:
        raise ValueError('Invalid GitHub release page URL')
    tag = unquote(address.path[len(prefix):])
    if not tag or tag in ('.', '..'):
        raise ValueError('Missing GitHub release tag')
    page = ReleasePage()
    page.feed(release.text)
    if page.body_count != 1:
        raise ValueError('Missing or ambiguous GitHub release notes')
    assets = get(base + '/releases/expanded_assets/' + quote(tag, safe=''))
    assets.raise_for_status()
    links = ReleasePage()
    links.feed(assets.text)
    asset_prefix = f'/{owner}/{repository}/releases/download/{quote(tag, safe="")}/'
    downloads = {}
    for link in links.links:
        url = urljoin(base + '/', link)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or not parsed.path.startswith(asset_prefix):
            continue
        name = unquote(parsed.path[len(asset_prefix):])
        if name and '/' not in name and '\\' not in name and name not in ('.', '..'):
            downloads[name] = {'name': name, 'browser_download_url': url}
    return {'tag_name': tag, 'body': page.markdown(), 'assets': list(downloads.values())}
