#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pandas~=3.0",
#   "playwright~=1.61",
#   "tabulate~=0.10",
#   "toon_format @ https://github.com/toon-format/toon-python/archive/refs/heads/main.zip",
# ]
# ///
"""Scrape my GitHub Stars lists and print them, sorted by repo count.

    ./.github/playwright_github_stars_lists.py                    # TOON (default)
    ./.github/playwright_github_stars_lists.py --format markdown  # what README.md uses
    GITHUB_REPOSITORY_OWNER=octocat ./.github/playwright_github_stars_lists.py

USER comes from $GITHUB_REPOSITORY_OWNER (auto-set in GitHub Actions). The webkit
browser binary is installed into the shared playwright cache on first run (its
progress goes to stderr, never stdout), so the `uv run` shebang above is all you
need — no separate `pip install` / `playwright install`.

Output is TOON by default (https://github.com/toon-format/spec, self-describing —
paste straight into prompts). Override with --format markdown|tsv|csv|jsonl,
truncate with --limit N. .github/workflows/README.yml calls --format markdown and
appends the table to README.md.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import pandas as pd
from playwright.async_api import Browser, Error, Locator, async_playwright
from toon_format import encode as toon_encode


BROWSER = "webkit"  # chromium, firefox, webkit
HOST = "https://github.com"
USER = os.environ["GITHUB_REPOSITORY_OWNER"]


@asynccontextmanager
async def new_page(browser: Browser):
    """Render JS scripts in a pre-crawled HTML using a headless browser."""
    # many init options like proxy: https://playwright.dev/python/docs/api/class-browser#browser-new-page
    page = await browser.new_page()
    try:
        yield page
    finally:
        await page.close()


async def launch_browser(pw) -> Browser:
    """Launch BROWSER, installing its binary into the shared cache on first run."""
    launcher = getattr(pw, BROWSER)
    try:
        return await launcher.launch()
    except Error:
        # uv builds a fresh env per run, so the browser binary may be missing —
        # install it (matching this env's playwright version) and retry once.
        # stdout is redirected to stderr so download progress never pollutes our
        # data output (the workflow pipes stdout into README.md). System libraries
        # (--with-deps) are provisioned by the workflow.
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", BROWSER],
            check=True,
            stdout=sys.stderr,
        )
        return await launcher.launch()


async def parse_stars_list(loc: Locator):
    data = {
        "name": loc.locator("//h3").text_content(),
        "link": loc.get_attribute("href"),
        "description": loc.locator("//span[contains(@class, 'color-fg-muted')]").text_content(),
        "stars": loc.locator("//div[contains(text(), 'repositories')]").text_content(),
    }
    data = dict(zip(data.keys(), await asyncio.gather(*data.values())))
    data["link"] = HOST + data["link"]
    data["description"] = data["description"].strip()
    data["stars"] = int(data["stars"].split()[0])
    return data


async def get_github_stars_lists(browser: Browser):
    async with new_page(browser) as page:
        await page.goto(f"{HOST}/{USER}?tab=stars")
        # print(await page.content())
        loc = page.locator("//a[contains(@href, '/lists/')]")
        coros = [parse_stars_list(loc.nth(i)) for i in range(await loc.count())]
        return await asyncio.gather(*coros)


async def scrape():
    async with async_playwright() as pw:
        browser = await launch_browser(pw)
        try:
            return await get_github_stars_lists(browser)
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=["toon", "markdown", "tsv", "csv", "jsonl"],
        default="toon",
    )
    parser.add_argument("--limit", type=int, default=None, help="Truncate output rows.")
    args = parser.parse_args()

    df = pd.DataFrame(asyncio.run(scrape())).sort_values("stars", ascending=False)
    if args.limit is not None:
        df = df.head(args.limit)

    if args.format == "markdown":
        # collapse name + link into a single Markdown link column
        df = df.assign(name=df.apply(lambda r: f"[{r['name']}]({r['link']})", axis=1)).drop(columns="link")
        sys.stdout.write(df.to_markdown(index=False, tablefmt="github") + "\n")
    elif args.format == "toon":
        records = json.loads(df.to_json(orient="records", date_format="iso"))
        sys.stdout.write(toon_encode(records) + "\n")
    elif args.format == "tsv":
        df.to_csv(sys.stdout, sep="\t", index=False)
    elif args.format == "csv":
        df.to_csv(sys.stdout, index=False)
    else:  # jsonl
        df.to_json(sys.stdout, orient="records", date_format="iso", lines=True)


if __name__ == "__main__":
    main()
