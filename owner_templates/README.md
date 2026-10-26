# Owner-supplied templates only

No bundled example templates are provided. The owner adds approved static builds:

- `owner_templates/registry.json`: array of `{ "id": "my-site", "label": "My site" }`
- `owner_templates/my-site/index.html`: built website root with relative asset paths
- `owner_templates/my-site/assets/...`: optional CSS/JS/images/fonts

The actual Install link in your own template must have attribute `data-apk-install`:

```html
<a data-apk-install aria-disabled="true">Install APK</a>
```

Optional dynamic labels use `data-release-title`, `data-release-package`,
`data-release-version`. The runtime automatically attaches an authenticated
APK URL to the Install button only after the upload passes signature checks.

Only owner-trusted template files may be placed in this directory. Do not let
Telegram users upload HTML, JavaScript, or templates. Do not upload secrets.

For React/Vite builds, use relative asset paths (`base: './'`) and put the
contents of `dist/` in `owner_templates/<id>/`. No templates appear in the
bot until the owner supplies their own bundles and registers them.
