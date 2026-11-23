#!/usr/bin/env python3
# kanopy site - Developed by acidvegas (https://github.com/acidvegas)
# build.py

'''Build docs.html from kanopy's README.md, so the docs never drift from the repo. Run from a gh-pages checkout: uv run --with markdown python _build/build.py /path/to/README.md'''

import argparse
import re

from pathlib import Path

import markdown


SITE = Path(__file__).parent.parent # the gh-pages root; this script lives in _build/


def build(readme: Path) -> str:
	'''
	Render the README from its Quick start section to the end, minus the mirrors footer, into the docs page.

	:param readme: Path to kanopy's README.md
	'''

	text = readme.read_text()
	text = text[text.index('## Quick start'):]
	text = re.split(r'\n---\n\s*\n###### Mirrors', text)[0]
	text = text.replace('<details>', '<details markdown="1">')

	md   = markdown.Markdown(extensions=['tables', 'fenced_code', 'md_in_html', 'toc'], extension_configs={'toc': {'slugify': lambda value, sep: re.sub(r'[^a-z0-9 -]', '', value.lower()).strip().replace(' ', '-')}})
	body = md.convert(text)
	body = re.sub(r'<table>', '<div class="table-wrap"><table>', body).replace('</table>', '</table></div>')
	body = re.sub(r'<(h[23]) id="([^"]+)">(.*?)</\1>', lambda m: f'<{m[1]} id="{m[2]}">{m[3]}<a class="anchor" href="#{m[2]}" aria-label="Link to this section">#</a></{m[1]}>', body)

	toc = []
	for level, slug, title in re.findall(r'<h([23]) id="([^"]+)">(.*?)<a class="anchor"', body):
		toc.append(f'<a{" class=\"sub\"" if level == "3" else ""} href="#{slug}">{re.sub(r"<[^>]+>", "", title)}</a>')

	page = (Path(__file__).parent / 'docs.template.html').read_text()

	return page.replace('{{toc}}', '\n\t\t\t'.join(toc)).replace('{{body}}', body)


def main():
	'''Parse arguments and write docs.html.'''

	parser = argparse.ArgumentParser(description='Build the kanopy docs page from the README')
	parser.add_argument('readme', type=Path, help='Path to kanopy\'s README.md')
	args = parser.parse_args()

	(SITE / 'docs.html').write_text(build(args.readme))
	print(f'wrote {SITE / "docs.html"}')


if __name__ == '__main__':
	main()
