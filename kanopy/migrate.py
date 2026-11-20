#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanopy/migrate.py

from . import database
from . import kanban


TABLES = ('users', 'projects', 'tasks', 'attachments', 'settings', 'sent', 'labels', 'task_labels', 'comments', 'links', 'sessions', 'tokens', 'invites', 'teams', 'team_members', 'shares', 'task_assignees', 'task_events', 'notifications', 'trash') # parents before the rows that reference them
BATCH  = 500 # rows per insert batch


def copy(source: str, dest: str, log=print) -> dict:
	'''
	Copy a whole board from one database to an empty one, SQLite or PostgreSQL either way; returns rows copied per table.

	:param source: SQLite path or postgresql:// URL to copy from
	:param dest: SQLite path or postgresql:// URL to copy into; must have no users yet
	:param log: Progress printer
	'''

	if str(source) == str(dest):
		raise ValueError('source and destination are the same database')

	kanban.init(source)
	kanban.init(dest)

	src, dst = kanban.connect(source), kanban.connect(dest)

	if dst.execute('SELECT 1 FROM users').fetchone() or dst.execute('SELECT 1 FROM tasks').fetchone():
		raise ValueError('the destination already has users or tasks; copy only into a new, empty board')

	counts = {}

	with dst:
		for table in reversed(TABLES): # the fresh board's Inbox and default labels make way for the copied ones
			dst.execute(f'DELETE FROM {table}')
		for table in TABLES:
			cols = [c for c in database.columns(src, table) if c in database.columns(dst, table)]
			cur  = src.execute(f'SELECT {", ".join(cols)} FROM {table}')
			at   = cols.index('parent_id') if table == 'projects' else None # a parent can have a higher id than its child, so parents are linked in a second pass
			done = 0
			while rows := cur.fetchmany(BATCH): # a batch at a time, so big attachment tables never sit in memory whole
				dst.executemany(f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({kanban.marks(cols)})', [tuple(None if i == at else v for i, v in enumerate(r)) for r in rows])
				done += len(rows)
			counts[table] = done
			log(f'{table:<15} {done:>8}')
		dst.executemany('UPDATE projects SET parent_id = ? WHERE id = ?', [(r['parent_id'], r['id']) for r in src.execute('SELECT id, parent_id FROM projects WHERE parent_id IS NOT NULL')])
		if dst.postgres: # identity counters continue after the copied ids
			for table in sorted(database.ID_TABLES):
				dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")

	for table, count in counts.items():
		if dst.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] != count:
			raise RuntimeError(f'{table} has a different row count after the copy')

	return counts



if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser(description='Copy a kanopy board between SQLite and PostgreSQL. The source is not changed; the destination must be a new, empty board.')
	parser.add_argument('source', help='SQLite file (data/kanban.db) or postgresql://user:pass@host/db')
	parser.add_argument('dest', help='SQLite file or postgresql:// URL to copy into')
	args = parser.parse_args()

	copy(args.source, args.dest)
	print('done; point KANOPY_DATABASE at the new database and restart kanopy')
