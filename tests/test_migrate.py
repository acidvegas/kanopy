#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_migrate.py

import unittest

from pathlib import Path

from kanopy import accounts
from kanopy import kanban
from kanopy import migrate

from .base import Base, POSTGRES


class TestMigrate(Base):
	'''Copying a board between databases.'''

	def fill(self):
		'''Put one of everything on the current board.'''

		ann = self.user('ann', admin=True)
		bob = self.user('bob')
		self.act(ann)
		child = kanban.project_save('Top/child', folder='~/c')['id']
		kanban.project_save('Later')
		kanban.project_save('Later/Top', next(p['id'] for p in kanban.projects() if p['path'] == 'Top')) # parent now has a higher id than its child
		a = self.task('Later/Top/child', 'a', description='- [ ] x', labels=['host:pi'], assignees=['ann'], due='2030-01-01', repeat='weekly')
		b = self.task('Later/Top/child', 'b')
		kanban.link_add(a['id'], 'blocked', b['id'])
		kanban.comment_add(a['id'], 'ann', 'note')
		kanban.attachment_add(a['id'], 'f.bin', 'application/pdf', b'\x00\x01\x02')
		kanban.task_update(a['id'], {'description': '- [x] x'})
		accounts.share_add(child, 'viewer', 'bob')
		accounts.team_create(ann, 'crew')
		accounts.token_create(bob, 'claude')
		kanban.task_delete(self.task('Later', 'gone')['id'])
		self.act(None)
		return a, b


	def snapshot(self, target: str) -> dict:
		'''
		Every table's rows, sorted, from one database.

		:param target: Database path or URL
		'''

		db = kanban.connect(target)

		return {t: sorted(tuple(bytes(v) if isinstance(v, memoryview) else v for v in r) for r in db.execute(f'SELECT * FROM {t}')) for t in migrate.TABLES}


	def round_trip(self, other: str):
		'''
		Copy the board to another database and back, checking nothing changed either way.

		:param other: The second database
		'''

		a, b  = self.fill()
		first = self.snapshot(kanban.DATABASE)
		migrate.copy(kanban.DATABASE, other, log=lambda *x: None)
		self.assertEqual(self.snapshot(other), first)
		back = str(Path(self.dir.name) / 'back.db')
		migrate.copy(other, back, log=lambda *x: None)
		self.assertEqual(self.snapshot(back), first)
		kanban.DATABASE = other # the copy works as a live board, and new ids continue after the copied ones
		new = self.task('Later', 'after')
		self.assertGreater(new['id'], b['id'])
		self.assertEqual(kanban.task(a['id'])['links'][0]['id'], b['id'])


	def test_sqlite_to_sqlite(self):
		'''A SQLite board copies to a new SQLite file unchanged.'''

		self.round_trip(str(Path(self.dir.name) / 'copy.db'))


	def test_small_batches(self):
		'''Tables bigger than one batch copy whole, a batch at a time.'''

		old, migrate.BATCH = migrate.BATCH, 2
		try:
			self.round_trip(str(Path(self.dir.name) / 'copy.db'))
		finally:
			migrate.BATCH = old


	@unittest.skipUnless(POSTGRES, 'needs KANOPY_TEST_DATABASE')
	def test_postgres_and_sqlite(self):
		'''A board survives PostgreSQL to SQLite and back to PostgreSQL.'''

		a, b  = self.fill()
		first = self.snapshot(POSTGRES)
		lite  = str(Path(self.dir.name) / 'copy.db')
		migrate.copy(POSTGRES, lite, log=lambda *x: None)
		self.assertEqual(self.snapshot(lite), first)
		with kanban.connect() as db:
			db.executescript('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
		migrate.copy(lite, POSTGRES, log=lambda *x: None)
		self.assertEqual(self.snapshot(POSTGRES), first)
		self.assertGreater(self.task('Later', 'after')['id'], b['id'])
		self.assertEqual(bytes(kanban.attachment_get(kanban.task(a['id'])['attachments'][0]['id'])['data']), b'\x00\x01\x02')


	def test_refuses_non_empty_destination(self):
		'''The destination must be a new board.'''

		self.fill()
		other = str(Path(self.dir.name) / 'copy.db')
		migrate.copy(kanban.DATABASE, other, log=lambda *x: None)
		with self.assertRaises(ValueError):
			migrate.copy(kanban.DATABASE, other, log=lambda *x: None)
		with self.assertRaises(ValueError):
			migrate.copy(kanban.DATABASE, kanban.DATABASE, log=lambda *x: None)



if __name__ == '__main__':
	unittest.main()
