#!/usr/bin/env python3
# kanopy site - Developed by acidvegas (https://github.com/acidvegas)
# api.py

'''Build api.html (and a copy of the spec) from kanopy's static/openapi.json, so the API page never drifts from the repo. Run from a gh-pages checkout: uv run --with markdown python _build/api.py /path/to/static/openapi.json'''

import argparse
import html
import json
import re

from pathlib import Path

import markdown


SITE = Path(__file__).parent.parent # the gh-pages root; this script lives in _build/


def md(text: str) -> str:
	'''
	Render a description's markdown.

	:param text: Markdown text
	'''

	return markdown.markdown(text or '')


def slug(text: str) -> str:
	'''
	Turn a heading into an anchor id.

	:param text: Heading text
	'''

	return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def kind(schema: dict) -> str:
	'''
	Describe a schema's type in a few words of HTML, linking to named schemas.

	:param schema: JSON schema
	'''

	if '$ref' in schema:
		name = schema['$ref'].split('/')[-1]
		return f'<a href="#schema-{slug(name)}">{name}</a>'
	if 'allOf' in schema:
		return ' + '.join(kind(part) for part in schema['allOf'])
	if 'enum' in schema:
		return ' | '.join(f'<code>{html.escape(json.dumps(v))}</code>' for v in schema['enum'])
	types = schema.get('type', 'any')
	types = types if isinstance(types, list) else [types]
	out   = []
	for t in types:
		if t == 'array':
			out.append(kind(schema.get('items', {})) + '[]')
		elif t == 'object' and 'additionalProperties' in schema:
			out.append('{id: ' + kind(schema['additionalProperties']) + '}')
		else:
			out.append(t)
	return ' | '.join(out)


def fields(schema: dict, spec: dict) -> list:
	'''
	Return (name, type, required, description) rows for an object schema, following one $ref.

	:param schema: JSON schema
	:param spec: The whole spec, for $ref lookups
	'''

	if '$ref' in schema:
		schema = spec['components']['schemas'][schema['$ref'].split('/')[-1]]
	if 'allOf' in schema:
		return [row for part in schema['allOf'] for row in fields(part, spec)]
	need = set(schema.get('required', []))
	return [(name, kind(prop), name in need, prop.get('description', '')) for name, prop in schema.get('properties', {}).items()]


def table(head: list, rows: list) -> str:
	'''
	Render a table in the site's table style.

	:param head: Column titles
	:param rows: Rows of HTML cells
	'''

	cells = ''.join(f'<tr>{"".join(f"<td>{c}</td>" for c in row)}</tr>' for row in rows)
	return f'<div class="table-wrap"><table><thead><tr>{"".join(f"<th>{h}</th>" for h in head)}</tr></thead><tbody>{cells}</tbody></table></div>'


def operation(path: str, method: str, op: dict, spec: dict) -> str:
	'''
	Render one endpoint.

	:param path: URL path
	:param method: HTTP method
	:param op: Its OpenAPI operation
	:param spec: The whole spec
	'''

	anchor = slug(f'{method} {path}')
	auth   = {None: '', '[]': '<p class="auth">No sign-in needed.</p>', '[{"session": []}]': '<p class="auth">Sign-in cookie only: API tokens are refused.</p>', '[{"token": []}]': '<p class="auth">API token only.</p>'}[json.dumps(op['security']) if 'security' in op else None]
	out    = [f'<h3 id="{anchor}"><span class="verb {method}">{method.upper()}</span> <code>{html.escape(path)}</code><a class="anchor" href="#{anchor}" aria-label="Link to this endpoint">#</a></h3>', f'<p>{html.escape(op["summary"])}.</p>', md(op.get('description', '')), auth]

	params = op.get('parameters', [])
	if params:
		out.append(table(['Parameter', 'In', 'Type', 'Description'], [[f'<code>{p["name"]}</code>' + (' <small>required</small>' if p.get('required') else ''), p['in'], kind(p['schema']), html.escape(p.get('description', ''))] for p in params]))

	for ctype, content in op.get('requestBody', {}).get('content', {}).items():
		rows = fields(content['schema'], spec)
		if rows:
			out.append(f'<p class="label">Body <code>{ctype}</code></p>')
			out.append(table(['Field', 'Type', 'Description'], [[f'<code>{name}</code>' + (' <small>required</small>' if req else ''), t, html.escape(desc)] for name, t, req, desc in rows]))
		else:
			out.append(f'<p class="label">Body <code>{ctype}</code>: {html.escape(content["schema"].get("description", kind(content["schema"])))}</p>')

	codes = []
	for code, resp in op['responses'].items():
		if '$ref' in resp:
			continue # the shared error responses are listed once, at the top
		schema = next(iter(resp.get('content', {}).values()), {}).get('schema')
		codes.append(f'<li><code>{code}</code> {html.escape(resp["description"])}' + (f': {kind(schema)}' if schema and code < '300' and resp['description'] == 'OK' else '') + '</li>')
	errors = [code for code, resp in op['responses'].items() if '$ref' in resp]
	out.append('<ul class="codes">' + ''.join(codes) + (f'<li>Errors: {", ".join(f"<code>{c}</code>" for c in errors)}</li>' if errors else '') + '</ul>')

	return '\n'.join(part for part in out if part)


def build(spec: dict) -> str:
	'''
	Render the whole API page.

	:param spec: The parsed openapi.json
	'''

	tags = {tag['name']: [] for tag in spec['tags']}
	for path, ops in spec['paths'].items():
		for method, op in ops.items():
			tags[op['tags'][0]].append((path, method, op))

	toc  = ['<a href="#overview">Overview</a>']
	body = [f'<h2 id="overview">Overview<a class="anchor" href="#overview" aria-label="Link to this section">#</a></h2>', md(spec['info']['description'])]
	for name, ops in tags.items():
		toc.append(f'<a href="#{slug(name)}">{name}</a>')
		body.append(f'<h2 id="{slug(name)}">{name}<a class="anchor" href="#{slug(name)}" aria-label="Link to this section">#</a></h2>')
		body += [operation(path, method, op, spec) for path, method, op in ops]

	toc.append('<a href="#schemas">Schemas</a>')
	body.append('<h2 id="schemas">Schemas<a class="anchor" href="#schemas" aria-label="Link to this section">#</a></h2>')
	for name, schema in spec['components']['schemas'].items():
		body.append(f'<h3 id="schema-{slug(name)}">{name}<a class="anchor" href="#schema-{slug(name)}" aria-label="Link to this schema">#</a></h3>')
		rows = fields(schema, spec)
		body.append(table(['Field', 'Type', 'Description'], [[f'<code>{n}</code>', t, html.escape(d)] for n, t, _, d in rows]) if rows else f'<p>{kind(schema)}</p>')

	page = (Path(__file__).parent / 'docs.template.html').read_text()
	page = page.replace('<title>Docs · kanopy</title>', '<title>API · kanopy</title>').replace('<a class="on" href="docs.html">Docs</a>', '<a href="docs.html">Docs</a>').replace('<a href="api.html">API</a>', '<a class="on" href="api.html">API</a>')
	page = page.replace('<h1>Documentation</h1>', '<h1>REST API</h1>').replace('<p>Documentation</p>', '<p>REST API</p>')
	page = re.sub(r'<p class="intro">.*?</p>', f'<p class="intro">kanopy {spec["info"]["version"]}. Every endpoint the web app uses. This page is built from <a href="openapi.json">openapi.json</a> (OpenAPI 3.1), which every kanopy also serves at <code>/openapi.json</code>.</p>', page, flags=re.S)

	return page.replace('{{toc}}', '\n\t\t\t'.join(toc)).replace('{{body}}', '\n'.join(body))


def main():
	'''Parse arguments and write api.html and openapi.json.'''

	parser = argparse.ArgumentParser(description='Build the kanopy API page from openapi.json')
	parser.add_argument('spec', type=Path, help='Path to kanopy\'s static/openapi.json')
	args = parser.parse_args()

	text = args.spec.read_text()
	(SITE / 'openapi.json').write_text(text)
	(SITE / 'api.html').write_text(build(json.loads(text)))
	print(f'wrote {SITE / "api.html"} and {SITE / "openapi.json"}')


if __name__ == '__main__':
	main()
