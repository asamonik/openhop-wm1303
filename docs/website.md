# Project website

The project website is a minimal static setup guide in `site/`: hardware
requirements, installation, Manager access, and service commands. It uses plain
HTML and CSS, without JavaScript, external fonts, or a site generator. The
hardware Manager remains a separate application on the device; the public site
does not connect to the radio or expose the management API.

## Local preview

From the repository root:

```bash
python3 -m unittest discover -s tests -p test_site.py -v
python3 scripts/build_site.py
python3 -m http.server 8080 --directory _site
```

Open `http://localhost:8080`. No Node packages or site generator are required.
The build stages only the HTML, stylesheet, and favicon in `_site/`, which is
ignored by Git. All site asset paths are relative so the page also works under
the `/openhop-wm1303/` project path.

## GitHub Pages setup

In the repository's **Settings → Pages → Build and deployment**, select
**GitHub Actions** as the source. This one-time repository setting must be enabled
by a repository administrator. The workflow's `GITHUB_TOKEN` does not enable
Pages automatically.

The **Deploy website to GitHub Pages** workflow validates and builds changes to
the website. Pushes to `main` deploy to the `github-pages` environment; pull
requests validate the artifact without deploying. After enabling Pages, use
**Actions → Deploy website to GitHub Pages → Run workflow** to deploy, or rerun
a failed deployment. The workflow's environment link is the published URL.

Only `_site/` is uploaded. Runtime code, local configuration, and repository
working files are not part of the public website artifact. The existing
software regression workflow is unchanged.
