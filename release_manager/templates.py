"""Owner-controlled HTML template registry and install-button binding.

No stock templates are bundled. Only an owner-supplied, registered static bundle
is selectable. Bundles should be built with relative asset paths.
"""
import json
import re
from html import escape
from pathlib import Path
from urllib.parse import quote

TEMPLATE_ID = re.compile(r'^[a-z][a-z0-9_-]{1,47}$')
SAFE_ASSET = {'.css', '.js', '.mjs', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.woff', '.woff2', '.ico', '.txt', '.map'}


def available_templates(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    registry = root / 'registry.json'
    if not registry.is_file():
        return {}
    value = json.loads(registry.read_text('utf-8'))
    if not isinstance(value, list):
        raise ValueError('Template registry must contain a list')
    templates = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Invalid template entry')
        identifier, label = item.get('id'), item.get('label')
        if not isinstance(identifier, str) or not TEMPLATE_ID.fullmatch(identifier):
            raise ValueError('Invalid template identifier')
        if not isinstance(label, str) or not 2 <= len(label) <= 100:
            raise ValueError('Invalid template label')
        if identifier in templates:
            raise ValueError('Duplicate template identifier')
        if not (root / identifier / 'index.html').is_file():
            # Incomplete templates aren't selectable.
            continue
        templates[identifier] = label
    return templates


def asset_path(root: Path, identifier: str, relative: str) -> Path | None:
    root = Path(root).resolve()
    if not TEMPLATE_ID.fullmatch(identifier) or identifier not in available_templates(root):
        return None
    target_root = (root / identifier).resolve()
    path = (target_root / relative).resolve()
    if not path.is_relative_to(target_root) or path.suffix.lower() not in SAFE_ASSET:
        return None
    return path if path.is_file() else None


def render(root: Path, identifier: str, title: str, release=None, download_path=None) -> str:
    registry = available_templates(root)
    if identifier not in registry:
        raise LookupError('Template unavailable')
    path = (Path(root) / identifier / 'index.html')
    html = path.read_text('utf-8')
    if '<html' not in html.lower() or '</body>' not in html.lower():
        raise ValueError('Template requires html/body tags')
    # Templates are *owner-supplied* and trusted static bundles. No untrusted
    # user HTML is allowed. The runtime modifies only explicitly annotated
    # install links; it cannot masquerade as an official app store.
    metadata = {
        'title': title,
        'package': release.package_name if release else '',
        'version': release.version_name if release else '',
        'sha256': release.sha256 if release else '',
        'signer': release.signer_sha256 if release else '',
        'download': download_path or '',
    }
    payload = json.dumps(metadata).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    # CSP is intentionally not restrictive here, because imported UI builds may
    # include inline assets. Templates are owner-controlled, never user files.
    base = f'<base href="/template-assets/{quote(identifier)}/">'
    script = f'''<script>
(() => {{
 const details = {payload};
 const update = () => {{
   document.querySelectorAll('[data-apk-install]').forEach(node => {{
     if (details.download) {{
       if (node.tagName === 'A') node.setAttribute('href', details.download);
       node.removeAttribute('aria-disabled');
       node.removeAttribute('disabled');
       if (node.tagName === 'A') node.setAttribute('download', '');
     }} else {{
       node.removeAttribute('href');
       node.setAttribute('aria-disabled', 'true');
       node.setAttribute('title', 'APK not yet available');
       node.onclick = (event) => event.preventDefault();
     }}
   }});
   document.querySelectorAll('[data-release-title]').forEach(node => {{ if (node.textContent !== details.title) node.textContent = details.title; }});
   document.querySelectorAll('[data-release-package]').forEach(node => {{ if (node.textContent !== details.package) node.textContent = details.package; }});
   document.querySelectorAll('[data-release-version]').forEach(node => {{ if (node.textContent !== details.version) node.textContent = details.version; }});
 }};
 document.addEventListener('click', event => {{
   const node = event.target.closest?.('[data-apk-install]');
   if (!node) return;
   event.preventDefault();
   event.stopImmediatePropagation();
   if (details.download) window.location.assign(details.download);
 }}, true);
 document.addEventListener('DOMContentLoaded', update);
 let count = 0;
 const observer = new MutationObserver(() => {{ if (count++ < 120) update(); else observer.disconnect(); }});
 observer.observe(document.documentElement, {{childList:true, subtree:true}});
 setTimeout(() => observer.disconnect(), 8000);
}})();
</script>'''
    notice = ('<aside role="note" style="padding:10px;background:#fff6d9;color:#222;'
              'font:14px system-ui;text-align:center;position:relative;z-index:2147483647">'
              'Independent developer APK demonstration. Not a Google Play or government download. '
              'Verify the app publisher before installing.</aside>')
    html = re.sub(r'(?i)<head([^>]*)>', lambda m: '<head' + m.group(1) + '>' +
                  '<meta name="robots" content="noindex,nofollow">' +
                  '<meta name="referrer" content="no-referrer">' + base, html, count=1)
    html = re.sub(r'(?i)</body>', lambda m: script + notice + '</body>', html, count=1)
    return html
