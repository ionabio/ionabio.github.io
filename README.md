# nabi.be

A personal workshop for useful tools, playful experiments, and future web apps.

## Local preview

No dependencies or build step. From this directory run:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open http://localhost:8000.

## Editing

- `index.html`: homepage text, project cards, navigation, and page metadata.
- `styles.css`: responsive layout, colours, and CSS illustrations.
- `favicon.svg`: browser icon.
- `CNAME`: custom domain; keep `nabi.be` here.

The homepage uses system fonts and no JavaScript or external asset requests. Decorative illustrations are hidden from assistive technology. It supports keyboard navigation and reduced-motion preferences.

To feature another app, add a project card inside `.project-grid` in `index.html`. Cards can link to a local project path or an independently hosted app on a subdomain.

## Publishing

GitHub Pages publishes the root of the `main` branch to https://nabi.be. Push a reviewed commit to `main` to deploy; no DNS changes or server configuration are needed. To undo a homepage release, revert its commit and push the revert.

Existing project directories (`sudokuPad/` and `saving/`) remain independent. Both apps are linked from the homepage; other existing files remain at their original addresses.
