// kanopy - Developed by acidvegas (https://github.com/acidvegas)
// static/utils.js

const $ = (sel) => document.querySelector(sel);

const ICONS = { chev: '<svg class="chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>' };


/* ---------- helpers ---------- */

async function api(method, url, body) {
	const headers = body ? { 'Content-Type': 'application/json' } : {};
	const res     = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
	if (res.status === 401 && !url.startsWith('/api/login')) {
		KB.me = null;
		authShow('login');
		throw new Error('sign in first');
	}
	const data = await res.json().catch(() => ({}));
	if (!res.ok) {
		const err = new Error(data.error || res.statusText);
		err.status = res.status;
		throw err;
	}
	return data;
}

function ask({ title, text = '', value = null, ok = 'OK', danger = false, path = false, secret = false }) {
	return new Promise((resolve) => {
		const dlg   = $('#dialog');
		const input = $('#dlg-input');
		$('#dlg-title').textContent = title;
		$('#dlg-text').textContent  = text;
		$('#dlg-ok').textContent    = ok;
		$('#dlg-ok').classList.toggle('danger', danger);
		input.hidden = value === null;
		input.type   = secret ? 'password' : 'text';
		input.value  = value ?? '';
		dlg.returnValue = '';
		dlg.showModal();
		if (!input.hidden) input.focus();
		dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok' ? (value === null ? true : answer(input.value, path, secret)) : null), { once: true });
	});
}

function answer(text, path, secret) {
	if (secret) return text;
	text = text.trim();
	return path ? text.replace(/^\/+|\/+$/g, '') : text;
}

function grip(handle, prop, target, size) {
	const saved = store(prop);
	if (saved) target.style.setProperty(prop, saved);
	handle.addEventListener('dblclick', () => { target.style.removeProperty(prop); store(prop, null); });
	handle.addEventListener('pointerdown', (e) => {
		e.preventDefault();
		handle.setPointerCapture(e.pointerId);
		document.body.classList.add('dragging');
		const move = (ev) => target.style.setProperty(prop, size(ev));
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', () => {
			handle.removeEventListener('pointermove', move);
			document.body.classList.remove('dragging');
			store(prop, target.style.getPropertyValue(prop));
		}, { once: true });
	});
}

async function guard(fn) {
	try { await fn(); } catch (err) { toast(err.message, true); }
}

function store(key, value) {
	try {
		if (value === undefined) return JSON.parse(localStorage.getItem('kanban:' + key));
		localStorage.setItem('kanban:' + key, JSON.stringify(value));
	} catch { return null; }
}

let toastTimer;
function toast(msg, error = false) {
	const el = $('#toast');
	el.textContent = msg;
	el.classList.toggle('error', error);
	el.hidden = false;
	clearTimeout(toastTimer);
	toastTimer = setTimeout(() => el.hidden = true, error ? 5000 : 2200);
}


const KB = {
	projects  : [],
	tasks     : [],
	labels    : [],
	project   : undefined,
	task      : null,
	doneLimit : 25,
	selecting : false,
	loadSeq   : 0,
	routeSeq  : 0,
	patchSeq  : 0,
	thumbs    : new Map(), // attachment id -> Promise of a blob URL, for the open task
	thumbsFor : null,
	picked    : new Set(),
	archived  : false,
	view      : 'board',
	month     : new Date(new Date().getFullYear(), new Date().getMonth(), 1),
	calSort   : [],
	collapsed : new Set(store('kbCollapsed') || [])
};

const STATUS = { todo: 'To-Do', doing: 'Doing', done: 'Done' };
const KINDS  = { blocking: 'blocks', blocked: 'blocked by', subtask: 'subtask', parenttask: 'parent', related: 'related', duplicates: 'duplicates', duplicateof: 'duplicate of', precedes: 'precedes', follows: 'follows' };
const PRIO   = ['none', 'low', 'medium', 'high', 'urgent', 'do now'];

const SVG = {
	chat  : '<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
	check : '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 5-6"/></svg>',
	clip  : '<svg viewBox="0 0 24 24"><path d="m21 12-8.5 8.5a5 5 0 0 1-7-7L14 5a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 8"/></svg>',
	down  : '<svg viewBox="0 0 24 24"><path d="M12 4v12M6 10l6 6 6-6M4 20h16"/></svg>',
	back  : '<svg viewBox="0 0 24 24"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
	expand: '<svg viewBox="0 0 24 24"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
	panel : '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/></svg>',
	prev  : '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
	next  : '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
	clock : '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
	edit  : '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
	plus  : '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
	proj  : '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/></svg>',
	trash : '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/></svg>',
	x     : '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>'
};

const esc     = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const linkify = (md) => md.replace(/(^|[\s(])#(\d+)\b/g, (m, pre, id) => `${pre}[#${id}](${taskHref(id)})`);
const mdHtml  = (text) => DOMPurify.sanitize(marked.parse(linkify(text)), { FORBID_ATTR: ['id', 'name', 'class', 'style'], FORBID_TAGS: ['style', 'form'] }); // user markup must not clobber or restyle the app's own elements
const pane    = () => KB.view === 'task' ? $('#taskpage') : $('#drawer');
const narrow  = () => matchMedia('(max-width: 800px)').matches;
const taskHref = (id) => KB.view === 'task' || narrow() ? `#/task/${id}` : `${kbBase()}/${id}`; // phones open the full page, not the side panel
const kbView  = () => KB.view === 'calendar' ? 'calendar' : 'board';
const kbBase  = () => `#/${kbView()}/${KB.project ?? 'all'}`;
const ymd     = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const project = (id) => KB.projects.find((p) => p.id === id);
const tint    = (id) => { let p = project(id); while (p && !p.color) p = project(p.parent_id); return p?.color || ''; };
const chip    = (l, extra = '') => { const m = KB.labels.find((x) => x.name === l); const c = m?.color; return `<span class="chip${m ? '' : ' foreign'}" data-ns="${esc(l.split(':')[0])}"${c ? ` style="--c:${c}"` : ''}>${esc(l)}${extra}</span>`; };
const COLORS  = ['#a78bfa', '#f472b6', '#f87171', '#fb923c', '#fbbf24', '#4ade80', '#2dd4bf', '#22d3ee', '#60a5fa', '#94a3b8'];
const size    = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const inbox   = () => KB.projects.find((p) => p.title === 'Inbox' && !p.parent_id && p.role >= 2) || KB.projects.find((p) => p.role >= 2);
const avatar  = (name) => {
	const u = (KB.users || []).find((x) => x.username === name);
	if (u?.avatar_at) return `<img class="av" src="/api/users/${u.id}/avatar?v=${encodeURIComponent(u.avatar_at)}" alt="" title="${esc(name)}">`;
	let h = 0;
	for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 997;
	return `<i class="av" style="--c:${COLORS[h % COLORS.length]}" title="${esc(name)}">${esc(name.slice(0, 2))}</i>`;
};


/* ---------- data ---------- */

async function kbLoad() {
	const seq   = ++KB.loadSeq;
	const query = KB.project ? `?project=${KB.project}` : '';
	const [p, t, l, me, u] = await Promise.all([api('GET', '/api/projects'), api('GET', '/api/tasks' + query), api('GET', '/api/labels'), api('GET', '/api/me'), api('GET', '/api/users')]);
	if (seq !== KB.loadSeq) return; // a newer load is on its way
	KB.projects = p.projects;
	KB.tasks    = t.tasks;
	KB.labels   = l.labels;
	KB.me       = me;
	KB.users    = u.users;
	meRender();
	kbTree();
	kbRender();
}

async function kbRefreshTask(id) {
	if (KB.task?.id !== id) return;
	KB.task = await api('GET', `/api/tasks/${id}`);
	kbDrawer();
}

async function kbTaskSaved(task) {
	KB.task = task;
	kbDrawer();
	await kbLoad();
}


/* ---------- sidebar ---------- */

function kbTree() {
	const nav    = $('#kb-tree');
	const open   = KB.projects.reduce((n, p) => n + p.open, 0);
	const hidden = (p) => p.path.split('/').slice(0, -1).some((_, i, a) => KB.collapsed.has(p.path.split('/').slice(0, i + 1).join('/')));
	const parent = (p) => KB.projects.some((c) => c.parent_id === p.id && (KB.archived || !c.archived));
	const mine   = $('#kb-filter').value.trim() === '@me';
	const words  = $('#kb-filter').value.toLowerCase().split(/\s+/).filter((w) => w && !/^[#@]|:/.test(w)); // plain words; #id, @user and labels leave the tree alone
	const hit    = (p) => words.every((w) => p.path.toLowerCase().includes(w));
	const keep   = words.length && KB.projects.some(hit) ? new Set(KB.projects.filter(hit).flatMap((p) => p.path.split('/').map((_, i, a) => a.slice(0, i + 1).join('/')))) : null; // matches plus their parents; no match = text is about tasks, show every project
	nav.innerHTML = `<a class="item${KB.project === null && !mine ? ' active' : ''}" href="#/${kbView()}/all" data-all="1" style="--depth:0">${SVG.proj}<span>All tasks</span><b class="count">${open}</b></a>` +
		`<a class="item${KB.project === null && mine ? ' active' : ''}" href="#/${kbView()}/all" data-mine="1" style="--depth:0">${avatar(KB.me?.username || '?')}<span>Assigned to me</span></a>`;
	for (const p of KB.projects) {
		if ((p.archived && !KB.archived && p.id !== KB.project) || (keep ? !keep.has(p.path) : hidden(p))) continue;
		const depth = p.path.split('/').length - 1;
		const chev  = parent(p) ? `<i class="chev-btn" data-path="${esc(p.path)}">${ICONS.chev}</i>` : '<i class="chev-pad"></i>';
		const dot   = p.color ? `<i class="dot" style="--c:${p.color}"></i>` : '';
		nav.insertAdjacentHTML('beforeend', `<a class="item project${p.id === KB.project ? ' active' : ''}${p.archived ? ' archived' : ''}${KB.collapsed.has(p.path) ? '' : ' open'}" href="#/${kbView()}/${p.id}" draggable="true" style="--depth:${depth}">${chev}${dot}<span>${esc(p.title)}</span>${p.open ? `<b class="count">${p.open}</b>` : ''}</a>`);
	}
	const archived = KB.projects.filter((p) => p.archived).length;
	if (archived) nav.insertAdjacentHTML('beforeend', `<button class="link-btn" id="kb-archived">${KB.archived ? 'Hide' : 'Show'} archived (${archived})</button>`);
}


/* ---------- board ---------- */

function kbMatch(task) {
	const terms = $('#kb-filter').value.toLowerCase().split(/\s+/).filter(Boolean).map((term) => term === '@me' && KB.me ? '@' + KB.me.username : term);
	const hay   = `#${task.id} ${task.title} ${task.project} ${task.labels.join(' ')} p${task.priority} ${task.assignees.map((a) => '@' + a).join(' ')}`.toLowerCase();
	return terms.every((term) => hay.includes(term));
}

function kbCard(t) {
	const today = new Date().toLocaleDateString('en-CA'); // local YYYY-MM-DD
	const due   = t.due ? t.due.slice(0, 10) : '';
	const late  = due && t.status !== 'done' ? (due < today ? ' late' : due === today ? ' soon' : '') : '';
	const proj  = KB.project === null || t.project_id !== KB.project ? `<span class="proj">${tint(t.project_id) ? `<i class="dot" style="--c:${tint(t.project_id)}"></i>` : ''}${esc(KB.project ? t.project.slice(project(KB.project).path.length + 1) : t.project)}</span>` : '';
	const foot  = [
		...t.labels.map((l) => chip(l)),
		due ? `<span class="due${late}">${SVG.clock}${new Date(due + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>` : '',
		t.checks[1] ? `<span class="${t.checks[0] === t.checks[1] ? 'ok' : ''}">${SVG.check}${t.checks[0]}/${t.checks[1]}</span>` : '',
		t.comments ? `<span>${SVG.chat}${t.comments}</span>` : '',
		t.files ? `<span>${SVG.clip}${t.files}</span>` : '',
		t.assignees.length ? `<span class="avs">${t.assignees.map(avatar).join('')}</span>` : ''
	].join('');
	return `<div class="card${t.id === KB.task?.id ? ' active' : ''}${KB.picked.has(t.id) ? ' picked' : ''}" data-id="${t.id}">
		<div class="meta"><span class="id">#${t.id}</span>${proj}${t.priority ? `<span class="prio p${t.priority}">${PRIO[t.priority]}</span>` : ''}<a class="open-new" href="#/task/${t.id}" target="_blank" rel="noopener" draggable="false" title="Open in a new tab" aria-label="Open #${t.id} in a new tab">${SVG.expand}</a></div>
		<div class="title">${esc(t.title)}</div>
		${foot ? `<div class="foot">${foot}</div>` : ''}
	</div>`;
}

function kbBoard() {
	const visible = KB.tasks.filter(kbMatch);
	for (const status of Object.keys(STATUS)) {
		let list = visible.filter((t) => t.status === status);
		const col = $(`.column[data-status="${status}"]`);
		col.querySelector('.count').textContent = list.length;
		if (status === 'done') {
			list = list.sort((a, b) => (b.done_at || '').localeCompare(a.done_at || ''));
			col.querySelector('.more').hidden = list.length <= KB.doneLimit;
			list = list.slice(0, KB.doneLimit);
		}
		col.querySelector('.cards').innerHTML = list.map(kbCard).join('');
	}
	const p = project(KB.project);
	if (!(p && $('#kb-desc .desc-input') && $('#kb-desc').dataset.pid === String(p.id))) descRender(p); // keep an open description edit
	bulkBar();
}

function kbBar() {
	const p = project(KB.project);
	$('#crumb').innerHTML = '';
	(p ? p.path.split('/') : ['All tasks']).forEach((part, i, parts) => {
		if (i) $('#crumb').insertAdjacentHTML('beforeend', '<span class="sep">/</span>');
		const span = document.createElement('span');
		span.textContent = part;
		if (i < parts.length - 1) span.className = 'dim';
		$('#crumb').append(span);
	});
	const page = { search: 'Search', settings: 'Settings', inbox: 'Inbox', task: KB.task ? `Task #${KB.task.id}` : 'Task' }[KB.view];
	if (page) $('#crumb').textContent = page;
	document.title = `${page || (p ? p.title : 'All tasks')} · Kanopy`;
	$('#kb-project-ops').hidden = !p || !!page || p.role < 3;
	$('#kb-delete').hidden      = !p || p.role < 4;
	$('#nav-inbox').classList.toggle('active', KB.view === 'inbox');
	$('#kb-new-task').hidden    = !!page;
	$('#kb-select').hidden      = KB.view !== 'board';
	$('#kb-select').classList.toggle('on', KB.selecting);
	$('#nav-search').classList.toggle('active', KB.view === 'search');
	$('#nav-settings').classList.toggle('active', KB.view === 'settings');
	$('#cal-ops').hidden        = KB.view !== 'calendar';
	document.querySelectorAll('.views a').forEach((a) => {
		a.classList.toggle('active', a.dataset.view === KB.view);
		a.href = `#/${a.dataset.view}/${KB.project ?? 'all'}`;
	});
	$('#kb-archive').title = p?.archived ? 'Unarchive project' : 'Archive project';
	$('#kb-archive').classList.toggle('on', !!p?.archived);
}

async function kbNewTask(status = 'todo') {
	const target = project(KB.project)?.role >= 2 ? project(KB.project) : inbox();
	if (!target) return toast('Create a project first, or ask for editor access to one', true);
	const title  = await ask({ title: 'New task', text: `In ${target.path}`, value: '', ok: 'Create' });
	if (!title) return;
	await guard(async () => {
		const task = await api('POST', '/api/tasks', { project_id: target.id, title, status });
		location.hash = taskHref(task.id);
	});
}


/* ---------- views ---------- */

function kbRender() {
	for (const v of ['board', 'calendar', 'search', 'settings']) $(`#${v}`).hidden = KB.view !== v;
	$('#taskpage').hidden = KB.view !== 'task';
	$('#inbox').hidden    = KB.view !== 'inbox';
	if (KB.view === 'inbox') inboxLoad();
	if (KB.view !== 'board') $('#bulk').hidden = true;
	if (KB.view === 'board') kbBoard();
	if (KB.view === 'calendar') kbCalendar();
	if (KB.view === 'search') searchFilters();
	if (KB.view === 'settings') {
		lblRender();
		if (!KB.setLoaded) { KB.setLoaded = true; accountLoad(); if (KB.me?.is_admin) setLoad(); }
	}
	kbBar();
}


/* ---------- calendar ---------- */

function kbCalendar() {
	const m      = KB.month;
	const start  = new Date(m.getFullYear(), m.getMonth(), 1 - new Date(m.getFullYear(), m.getMonth(), 1).getDay());
	const today  = ymd(new Date());
	const events = {};
	for (const t of KB.tasks.filter(kbMatch)) {
		if (t.due) (events[t.due.slice(0, 10)] ??= []).push({ t, kind: 'due' });
		if (t.remind_at) (events[t.remind_at.slice(0, 10)] ??= []).push({ t, kind: 'remind' });
	}
	$('#cal-title').textContent = m.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
	let html = [0, 1, 2, 3, 4, 5, 6].map((i) => `<div class="dow">${new Date(2026, 1, i + 1).toLocaleDateString(undefined, { weekday: 'short' })}</div>`).join('');
	for (let i = 0; i < 42; i++) {
		const d    = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
		const key  = ymd(d);
		const evs  = (events[key] || []).map(({ t, kind }) => {
			const c    = tint(t.project_id);
			const late = kind === 'due' && t.status !== 'done' && key < today ? ' late' : '';
			return `<div class="ev ${kind} ${t.status}${late}" data-id="${t.id}" data-kind="${kind}" title="#${t.id} ${esc(t.title)} (${esc(t.project)})">${c ? `<i class="dot" style="--c:${c}"></i>` : ''}${kind === 'remind' ? `<b>${t.remind_at.slice(11, 16)}</b>` : ''}<span>${esc(t.title)}</span></div>`;
		}).join('');
		html += `<div class="day${d.getMonth() !== m.getMonth() ? ' other' : ''}${key === today ? ' today' : ''}"><span class="num">${d.getDate()}</span><div class="evs" data-date="${key}">${evs}</div></div>`;
	}
	$('#cal-grid').innerHTML = html;
	KB.calSort.forEach((s) => s.destroy());
	KB.calSort = [...document.querySelectorAll('#cal-grid .evs')].map((el) => new Sortable(el, {
		group: 'cal', sort: false, animation: 150, delay: 150, delayOnTouchOnly: true, ghostClass: 'ghost',
		onEnd: (evt) => {
			const date = evt.to.dataset.date;
			if (evt.from === evt.to) return;
			const t     = KB.tasks.find((x) => x.id === +evt.item.dataset.id);
			const field = evt.item.dataset.kind === 'due' ? { due: date } : { remind_at: `${date}T${t.remind_at.slice(11, 16)}` };
			guard(async () => {
				await api('PATCH', `/api/tasks/${t.id}`, field);
				await kbLoad();
				await kbRefreshTask(t.id);
				toast(`#${t.id} ${evt.item.dataset.kind === 'due' ? 'due' : 'reminder'} moved to ${date}`);
			});
		}
	}));
}

function kbMonth(delta) {
	KB.month = delta === 0 ? new Date(new Date().getFullYear(), new Date().getMonth(), 1) : new Date(KB.month.getFullYear(), KB.month.getMonth() + delta, 1);
	kbCalendar();
}


/* ---------- labels ---------- */

async function lblNew(glob) {
	const name = await ask({ title: glob ? 'New global label' : 'New label', text: glob ? 'namespace:value; everyone sees it' : 'namespace:value, like host:pi; only you see it', value: '', ok: 'Create' });
	if (name) await guard(async () => { await api('POST', '/api/labels', { name, global: glob }); await kbLoad(); toast(`Created ${name}`); });
}

function lblRender() {
	$('#labels').innerHTML = [['Global', true], ['Mine', false]].map(([title, glob]) => {
		const groups = {};
		for (const l of KB.labels.filter((x) => x.global === glob)) (groups[l.name.split(':')[0]] ??= []).push(l);
		return `<h3 class="lbl-scope">${title}</h3>` + (lblGroups(groups, !glob || KB.me?.is_admin) || '<p class="dim">No labels yet.</p>');
	}).join('');
}

function lblGroups(groups, mine) {
	return Object.entries(groups).sort().map(([ns, list]) => `
		<section class="lbl-group">
			<h3>${esc(ns)}: <span class="dim">${list.length}</span></h3>
			<div class="lbl-list">${list.map((l) => `
				<div class="lbl-row" data-name="${esc(l.name)}">
					<button class="swatch${l.color ? '' : ' none'}" style="--c:${l.color || 'transparent'}" data-act="lcolor" title="Change color" aria-label="Change color"${mine ? '' : ' disabled'}></button>
					${chip(l.name)}
					<a class="lbl-count" href="#/board/all" data-act="lfilter">${l.count} task${l.count === 1 ? '' : 's'}</a>
					<span class="spacer"></span>
					${mine ? `<button class="icon small" data-act="lrename" title="Rename / merge" aria-label="Rename / merge">${SVG.edit}</button>
					<button class="icon small danger" data-act="ldelete" title="Delete label" aria-label="Delete label">${SVG.trash}</button>` : ''}
				</div>`).join('')}
			</div>
		</section>`).join('');
}

async function lblAction(act, el) {
	const name = el.closest('.lbl-row').dataset.name;
	const l    = KB.labels.find((x) => x.name === name);
	if (act === 'lfilter') {
		$('#kb-filter').value = name;
		KB.project = undefined;
		return;
	}
	if (act === 'lcolor') {
		const box  = $('#lbl-palette');
		const rect = el.getBoundingClientRect();
		box.dataset.name = name;
		box.innerHTML = COLORS.map((c) => `<button class="swatch${l.color === c ? ' on' : ''}" data-color="${c}" style="--c:${c}" aria-label="${c}"></button>`).join('') + `<button class="swatch none${l.color ? '' : ' on'}" data-color="" title="No color" aria-label="No color"></button>`;
		box.style.left = `${rect.left}px`;
		box.style.top  = `${rect.bottom + 6}px`;
		box.hidden = false;
		return;
	}
	if (act === 'lrename') {
		const next = await ask({ title: 'Rename label', text: 'Use an existing label name to merge into it.', value: name, ok: 'Save' });
		if (!next || next.toLowerCase() === name) return;
		const target = KB.labels.find((x) => x.name === next.toLowerCase());
		if (target && !await ask({ title: `Merge into ${target.name}?`, text: `${l.count} task(s) move from ${name} to ${target.name}, and ${name} is deleted.`, ok: 'Merge', danger: true })) return;
		return guard(async () => { await api('PATCH', '/api/labels', { name, new_name: next }); await kbLoad(); toast(target ? `Merged into ${target.name}` : `Renamed to ${next.toLowerCase()}`); });
	}
	if (act === 'ldelete') {
		if (!await ask({ title: `Delete ${name}?`, text: `It is removed from ${l.count} task(s). This cannot be undone.`, ok: 'Delete', danger: true })) return;
		return guard(async () => { await api('DELETE', `/api/labels?name=${encodeURIComponent(name)}`); await kbLoad(); toast(`Deleted ${name}`); });
	}
}


/* ---------- project description ---------- */

function descRender(p, editing = false) {
	const box = $('#kb-desc');
	box.hidden = !p;
	box.dataset.pid = p ? p.id : '';
	if (!p) return;
	const open = !store('descCollapsed');
	if (editing) {
		const swatches = COLORS.map((c) => `<button class="swatch${p.color === c ? ' on' : ''}" data-desc="color" data-color="${c}" style="--c:${c}" aria-label="${c}"></button>`).join('') + `<button class="swatch none${p.color ? '' : ' on'}" data-desc="color" data-color="" title="No color" aria-label="No color"></button>`;
		box.innerHTML = `<div class="desc-head"><b>Description</b></div>${mdEditor('desc-input', p.description, 'Markdown. One line on what this is.', 5)}<label class="desc-folder"><b>Folder</b><input class="folder-input" value="${esc(p.folder)}" placeholder="~/code/... for repo projects" spellcheck="false" autocomplete="off"></label><div class="desc-colors"><b>Color</b>${swatches}</div><div class="row end"><button data-desc="cancel">Cancel</button><button class="primary" data-desc="save">Save</button></div>`;
		const input = box.querySelector('textarea');
		input.style.height = Math.max(110, input.scrollHeight + 4) + 'px';
		return input.focus();
	}
	box.innerHTML = `<div class="desc-head"><button class="desc-toggle${open ? ' open' : ''}" data-desc="toggle">${ICONS.chev}<b>Description</b></button>${p.folder ? `<code class="desc-path" title="Folder">${esc(p.folder)}</code>` : ''}<span class="spacer"></span><button class="icon small" data-desc="edit" title="Edit description and color" aria-label="Edit description and color">${SVG.edit}</button></div>` +
		(open ? `<div class="markdown desc-body">${p.description.trim() ? mdHtml(p.description) : '<p class="dim">No description yet. Click the pencil to add one.</p>'}</div>` : '');
}


/* ---------- search ---------- */

let searchTimer;
function searchFilters() {
	const form = $('#search-filters');
	const keep = (el, html) => { const v = el.value; el.innerHTML = html; el.value = [...el.options].some((o) => o.value === v) ? v : ''; };
	keep(form.elements.project, '<option value="">All projects</option>' + KB.projects.map((p) => `<option value="${p.id}">${esc(p.path)}</option>`).join(''));
	keep(form.elements.assignee, '<option value="">Anyone</option><option value="me">Me</option>' + KB.users.map((u) => `<option value="${esc(u.username)}">${esc(u.username)}</option>`).join(''));
	keep(form.elements.label, '<option value="">Any label</option>' + KB.labels.map((l) => `<option value="${esc(l.name)}">${esc(l.name)}</option>`).join(''));
}

async function searchRun() {
	const q      = $('#search-q').value.trim();
	const form   = $('#search-filters');
	const box    = $('#search-results');
	const picked = (name) => [...form.querySelectorAll(`[name=${name}]:checked`)].map((el) => el.value);
	const status = picked('status');
	const fields = picked('fields');
	const who    = form.elements.assignee.value === 'me' ? KB.me.username : form.elements.assignee.value;
	const params = new URLSearchParams({ q, assignee: who, project: form.elements.project.value, label: form.elements.label.value, priority: form.elements.priority.value, due: form.elements.due.value, sort: form.elements.sort.value, status: status.length === 3 ? '' : status.join(','), fields: fields.join(',') });
	const narrow = params.get('assignee') || params.get('project') || params.get('label') || params.get('priority') !== '0' || params.get('due') || params.get('status');
	if (!q && !narrow) return box.innerHTML = '<p class="dim">Type to search every task, or pick a filter to list tasks.</p>';
	if (!status.length) return box.innerHTML = '<p class="dim">Pick at least one status.</p>';
	const query = params.toString();
	searchRun.last = query;
	const { results } = await api('GET', `/api/search?${query}`);
	if (searchRun.last !== query) return;
	const words = q.split(/\s+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
	const mark  = (text) => words.length ? String(text ?? '').split(new RegExp(`(${words.join('|')})`, 'gi')).map((part, i) => i % 2 ? `<mark>${esc(part)}</mark>` : esc(part)).join('') : esc(text);
	box.innerHTML = results.length ? `<p class="dim">${results.length}${results.length === 300 ? '+' : ''} result${results.length === 1 ? '' : 's'}</p>` + results.map((r) => {
		const c = tint(r.project_id);
		return `<a class="hit ${r.status}" href="#/task/${r.id}">
			<div class="meta"><span class="id">#${r.id}</span>${c ? `<i class="dot" style="--c:${c}"></i>` : ''}<span>${esc(r.project)}</span>${r.due ? `<span>due ${r.due}</span>` : ''}${r.priority ? `<span class="prio p${r.priority}">${PRIO[r.priority]}</span>` : ''}${r.archived ? '<span class="st">Archived</span>' : ''}<span class="st st-${r.status}">${STATUS[r.status]}</span></div>
			<div class="title">${mark(r.title)}</div>
			${r.snippet ? `<div class="snip"><span class="dim">${r.where}:</span> ${mark(r.snippet)}</div>` : ''}
			${r.labels.length ? `<div class="hit-labels">${r.labels.map((l) => chip(l)).join('')}</div>` : ''}
		</a>`;
	}).join('') : '<p class="dim">No matches.</p>';
}


/* ---------- settings ---------- */

async function setLoad() {
	const conf = await api('GET', '/api/settings');
	const form = $('#settings-form');
	for (const el of form.elements) {
		if (!el.name || el.name === 'clear_token') continue;
		if (el.type === 'checkbox') el.checked = conf[el.name] === '1';
		else el.value = el.name === 'ntfy_token' ? '' : conf[el.name] ?? '';
	}
	$('#board-form').elements.archive_days.value = conf.archive_days;
	form.elements.ntfy_token.placeholder = conf.token_set ? '••••••••  (blank keeps it)' : 'not set';
	form.elements.clear_token.value = '';
	$('#set-clear').hidden = !conf.token_set;
	const on = ['notify_remind', 'notify_due', 'notify_digest', 'notify_agent_done'].filter((k) => conf[k] === '1').length;
	$('#set-status').textContent = conf.ntfy_topic ? `${conf.ntfy_topic} · ${on}/4 on` : 'no topic';
	$('#set-status').classList.toggle('off', !conf.ntfy_topic || !on);
}

async function setSave() {
	const form = $('#settings-form');
	const body = {};
	for (const el of form.elements) {
		if (!el.name) continue;
		body[el.name] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
	}
	body.clear_token = form.elements.clear_token.value === '1';
	await api('PUT', '/api/settings', body);
	await setLoad();
	toast('Settings saved');
}


/* ---------- sidebar drag and drop ---------- */

function dndBind() {
	const tree  = $('#kb-tree');
	const clear = () => tree.querySelectorAll('.drop-before, .drop-after, .drop-into').forEach((el) => el.classList.remove('drop-before', 'drop-after', 'drop-into'));
	const zone  = (e, el) => {
		if (KB.dragTask || !el.classList.contains('project')) return 'into';
		const r = el.getBoundingClientRect(), y = (e.clientY - r.top) / r.height;
		return y < .3 ? 'before' : y > .7 ? 'after' : 'into';
	};
	tree.addEventListener('dragstart', (e) => {
		const el = e.target.closest('.item.project');
		if (!el) return;
		KB.dragProject = +el.href.split('/').pop();
		e.dataTransfer.effectAllowed = 'move';
		e.dataTransfer.setData('text/plain', String(KB.dragProject));
	});
	tree.addEventListener('dragend', () => { KB.dragProject = null; clear(); });
	tree.addEventListener('dragover', (e) => {
		const el = e.target.closest('.item');
		if (!el || !(KB.dragProject || KB.dragTask)) return;
		e.preventDefault();
		clear();
		el.classList.add(`drop-${zone(e, el)}`);
	});
	tree.addEventListener('dragleave', (e) => { if (!tree.contains(e.relatedTarget)) clear(); });
	tree.addEventListener('drop', (e) => {
		const el = e.target.closest('.item');
		if (!el) return;
		e.preventDefault();
		const where  = zone(e, el);
		const target = el.classList.contains('project') ? +el.href.split('/').pop() : null;
		clear();
		if (KB.dragTask) {
			const id = KB.dragTask;
			KB.dragTask = null;
			if (!target) return;
			return guard(async () => { await api('PATCH', `/api/tasks/${id}`, { project_id: target }); await kbLoad(); await kbRefreshTask(id); toast(`Moved #${id} to ${project(target).path}`); });
		}
		const moving = KB.dragProject;
		KB.dragProject = null;
		if (!moving || moving === target) return;
		const parent = !target ? null : where === 'into' ? target : project(target).parent_id;
		const ids    = KB.projects.filter((p) => p.parent_id === parent && p.id !== moving).map((p) => p.id);
		if (where === 'into' || !target) ids.push(moving);
		else ids.splice(ids.indexOf(target) + (where === 'after' ? 1 : 0), 0, moving);
		guard(async () => { await api('POST', '/api/projects/order', { parent_id: parent, ids }); await kbLoad(); });
	});
}


/* ---------- bulk select ---------- */

async function bulkApply(change, done) {
	const ids = [...KB.picked];
	if (!ids.length) return toast('Select some tasks first', true);
	await guard(async () => {
		await api('POST', '/api/tasks/bulk', { ids, ...change });
		await kbLoad();
		toast(done);
	});
}

function bulkBar() {
	const bar = $('#bulk');
	bar.hidden = !KB.selecting || KB.view !== 'board';
	if (bar.hidden) return;
	const tasks = KB.tasks.filter((t) => KB.picked.has(t.id));
	const used  = [...new Set(tasks.flatMap((t) => t.labels))].sort();
	$('#bulk-count').textContent = `${KB.picked.size} selected`;
	$('#bulk-status').value = '';
	$('#bulk-project').innerHTML = '<option value="">Move to project</option>' + KB.projects.filter((p) => !p.archived).map((p) => `<option value="${p.id}">${esc(p.path)}</option>`).join('');
	$('#bulk-labels').innerHTML = KB.labels.map((l) => `<option value="${esc(l.name)}">`).join('');
	$('#bulk-label').value = '';
	$('#bulk-unlabel').innerHTML = '<option value="">Remove label</option>' + used.map((l) => `<option value="${esc(l)}">${esc(l)}</option>`).join('');
	$('#bulk-unlabel').disabled = !used.length;
}

function bulkMode(on) {
	KB.selecting = on;
	KB.picked.clear();
	document.body.classList.toggle('selecting', on);
	if (on && KB.task) location.hash = kbBase();
	kbBar();
	kbBoard();
}


/* ---------- attachments ---------- */

function thumbsFree() {
	for (const p of KB.thumbs.values()) p.then(URL.revokeObjectURL).catch(() => {});
	KB.thumbs.clear();
	KB.thumbsFor = null;
}

async function fileBlob(id) {
	const res = await fetch(`/api/attachments/${id}`);
	if (!res.ok) throw new Error(`download failed (${res.status})`);
	return URL.createObjectURL(await res.blob());
}

async function fileUpload(files) {
	const id   = KB.task.id;
	const form = new FormData();
	for (const f of files) form.append('file', f);
	await guard(async () => {
		toast(`Uploading ${files.length} file${files.length === 1 ? '' : 's'}...`);
		const res  = await fetch(`/api/tasks/${id}/attachments`, { method: 'POST', body: form });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) throw new Error(data.error || (res.status === 413 ? 'upload too large' : res.statusText));
		if (KB.task?.id === id) await kbTaskSaved(data);
		toast('Uploaded');
	});
}


/* ---------- themes ---------- */

const THEMES = [ // key, name, panel, bg, accent, accent2, text, muted
	['', 'Purple (default)', '#0c0a16', '#07060d', '#a78bfa', '#f472b6', '#e6e3f2', '#8a85a8'],
	['dracula', 'Dracula', '#282a36', '#21222c', '#bd93f9', '#ff79c6', '#f8f8f2', '#9ea4c8'],
	['nord', 'Nord', '#2e3440', '#242933', '#88c0d0', '#81a1c1', '#eceff4', '#9aa5b8'],
	['gruvbox', 'Gruvbox', '#282828', '#1d2021', '#fabd2f', '#fe8019', '#ebdbb2', '#a89984'],
	['catppuccin', 'Catppuccin Mocha', '#181825', '#11111b', '#cba6f7', '#f5c2e7', '#cdd6f4', '#9399b2'],
	['tokyonight', 'Tokyo Night', '#1a1b26', '#16161e', '#7aa2f7', '#bb9af7', '#c0caf5', '#7f86ad'],
	['onedark', 'One Dark', '#21252b', '#1e2127', '#61afef', '#c678dd', '#abb2bf', '#7f848e'],
	['monokai', 'Monokai', '#272822', '#1e1f1c', '#a6e22e', '#66d9ef', '#f8f8f2', '#90908a'],
	['githubdark', 'GitHub Dark', '#161b22', '#0d1117', '#58a6ff', '#bc8cff', '#e6edf3', '#8b949e'],
	['github', 'GitHub Light', '#ffffff', '#f6f8fa', '#0969da', '#8250df', '#1f2328', '#656d76']
];

function themeRender() {
	const cur = document.documentElement.dataset.theme || '';
	$('#themes').innerHTML = THEMES.map(([key, name, panel, bg, accent, accent2, text, muted]) => `<button type="button" class="theme${key === cur ? ' on' : ''}" data-theme-key="${key}">
		<span class="theme-preview" style="background:${bg}"><i style="background:${panel}"></i><span class="tp-main2"><span class="bar" style="background:linear-gradient(90deg, ${accent}, ${accent2}); width: 60%"></span><span class="bar" style="background:${text}; width: 85%; opacity: .8"></span><span class="bar" style="background:${muted}; width: 45%"></span></span></span>
		<span class="theme-name">${esc(name)}</span>
	</button>`).join('');
}

function themeSet(key) {
	document.documentElement.dataset.theme = key;
	document.cookie = `kb_theme=${key}; max-age=${key ? 31536000 : 0}; path=/; samesite=lax`;
	window.kbLogo(key);
	themeRender();
}


/* ---------- accounts ---------- */

async function accountLoad() {
	const form = $('#profile-form');
	themeRender();
	$('#me-name').textContent = KB.me.username + (KB.me.is_admin ? ' (admin)' : '');
	$('#me-avatar').innerHTML = avatar(KB.me.username);
	$('#avatar-clear').hidden = !KB.me.avatar_at;
	form.elements.ntfy_topic.value = KB.me.ntfy_topic;
	form.elements.current_password.value = form.elements.password.value = '';
	for (const k of ['notify_mention', 'notify_assign', 'notify_activity', 'notify_due']) form.elements[k].checked = !!KB.me[k];
	const [teams, tokens] = await Promise.all([api('GET', '/api/teams'), api('GET', '/api/tokens')]);
	KB.teams = teams.teams;
	teamsRender();
	tokensRender(tokens.tokens);
	if (KB.me.is_admin) usersLoad();
}

function accountBind() {
	$('#auth-form').addEventListener('submit', (e) => { e.preventDefault(); authSubmit(); });
	$('#themes').addEventListener('click', (e) => { const btn = e.target.closest('[data-theme-key]'); if (btn) themeSet(btn.dataset.themeKey); });
	$('#avatar-pick').onclick = () => $('#avatar-file').click();
	$('#avatar-file').onchange = (e) => { const file = e.target.files[0]; e.target.value = ''; if (file) guard(() => avatarUpload(file)); };
	$('#avatar-clear').onclick = () => guard(async () => { KB.me = { ...KB.me, ...await api('DELETE', '/api/me/avatar') }; await kbLoad(); accountLoad(); toast('Picture removed'); });
	$('#logout').onclick = () => guard(async () => { await api('POST', '/api/logout'); location.reload(); });
	$('#profile-form').addEventListener('submit', (e) => {
		e.preventDefault();
		const f    = e.target.elements;
		const body = { ntfy_topic: f.ntfy_topic.value };
		for (const k of ['notify_mention', 'notify_assign', 'notify_activity', 'notify_due']) body[k] = f[k].checked;
		if (f.password.value) Object.assign(body, { password: f.password.value, current_password: f.current_password.value });
		guard(async () => { KB.me = { ...KB.me, ...await api('PATCH', '/api/me', body) }; f.password.value = f.current_password.value = ''; toast('Account saved'); });
	});
	$('#team-new').addEventListener('submit', (e) => {
		e.preventDefault();
		guard(async () => { KB.teams = (await api('POST', '/api/teams', { name: e.target.elements.name.value })).teams; e.target.reset(); teamsRender(); });
	});
	$('#teams').addEventListener('submit', (e) => {
		e.preventDefault();
		const id = e.target.closest('.team').dataset.id;
		guard(async () => { KB.teams = (await api('POST', `/api/teams/${id}/members`, { username: e.target.elements.username.value })).teams; teamsRender(); });
	});
	$('#teams').addEventListener('click', async (e) => {
		const btn = e.target.closest('[data-team]');
		if (!btn) return;
		const id   = btn.closest('.team').dataset.id;
		const user = btn.dataset.user;
		const act  = btn.dataset.team;
		if (act === 'delete' && !await ask({ title: 'Delete this team?', text: 'Projects shared with it stop being shared with its members.', ok: 'Delete', danger: true })) return;
		guard(async () => {
			const res = act === 'delete' ? await api('DELETE', `/api/teams/${id}`)
				: act === 'remove' || act === 'leave' ? await api('DELETE', `/api/teams/${id}/members/${user || KB.me.id}`)
				: await api('PATCH', `/api/teams/${id}/members/${user}`, { owner: act === 'promote' });
			KB.teams = res.teams;
			teamsRender();
		});
	});
	$('#token-new').addEventListener('submit', (e) => {
		e.preventDefault();
		guard(async () => {
			const t = await api('POST', '/api/tokens', { name: e.target.elements.name.value });
			e.target.reset();
			secretShow($('#token-shown'), 'Copy this token now; it is not shown again.', t.token, `claude mcp add --transport http -s user kanopy ${location.origin}/mcp --header "X-Agent: claude" --header "X-Key: ${t.token}"`);
			tokensRender((await api('GET', '/api/tokens')).tokens);
		});
	});
	$('#tokens').addEventListener('click', async (e) => {
		const btn = e.target.closest('[data-token]');
		if (!btn || !await ask({ title: 'Revoke this token?', text: 'Agents using it stop working.', ok: 'Revoke', danger: true })) return;
		guard(async () => { await api('DELETE', `/api/tokens/${btn.dataset.token}`); tokensRender((await api('GET', '/api/tokens')).tokens); });
	});
	$('#invite-new').onclick = () => guard(async () => {
		const inv = await api('POST', '/api/invites');
		secretShow($('#invite-shown'), 'Send this link to the person you are inviting. It works once and expires in 7 days.', inv.url);
		usersLoad();
	});
	$('#invites').addEventListener('click', (e) => {
		const btn = e.target.closest('[data-invite]');
		if (btn) guard(async () => { await api('DELETE', `/api/invites/${btn.dataset.invite}`); usersLoad(); });
	});
	$('#users').addEventListener('click', async (e) => {
		const btn = e.target.closest('[data-user-act]');
		if (!btn) return;
		const id = btn.dataset.id, name = btn.dataset.name;
		if (btn.dataset.userAct === 'reset') {
			const pw = await ask({ title: `New password for ${name}`, text: 'At least 8 characters. They are signed out everywhere.', value: '', ok: 'Set password', secret: true });
			if (pw) guard(async () => { await api('POST', `/api/users/${id}/password`, { password: pw }); toast(`Password set for ${name}`); });
		} else if (await ask({ title: `Delete ${name}?`, text: 'Their projects move to you; their assignments, comments stay. This cannot be undone.', ok: 'Delete', danger: true })) {
			guard(async () => { await api('DELETE', `/api/users/${id}`); usersLoad(); });
		}
	});
	$('#kb-share').onclick = () => shareOpen();
	$('#share-add').onclick = () => shareAdd();
	$('#share-who').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); shareAdd(); } });
	$('#share-list').addEventListener('click', (e) => {
		const btn = e.target.closest('[data-share-del]');
		if (btn) guard(async () => shareRender((await api('DELETE', `/api/shares/${btn.dataset.shareDel}`)).shares));
	});
	$('#share-list').addEventListener('change', (e) => {
		const sel = e.target.closest('[data-share-role]');
		if (sel) guard(async () => shareRender((await api('POST', `/api/projects/${KB.project}/shares`, { [sel.dataset.kind === 'team' ? 'team' : 'username']: sel.dataset.name, role: sel.value })).shares));
	});
	$('#inbox').addEventListener('click', (e) => {
		if (e.target.closest('#inbox-read')) return guard(async () => { inboxRender(await api('POST', '/api/notifications/read', {})); KB.me.unread = 0; meRender(); });
		const row = e.target.closest('.note[data-id]');
		if (row && row.classList.contains('unread')) api('POST', '/api/notifications/read', { ids: [+row.dataset.id] }).then((n) => { KB.me.unread = n.unread; meRender(); });
	});
	$('#kb-tree').addEventListener('click', (e) => {
		if (e.target.closest('[data-mine]')) $('#kb-filter').value = '@me';
		else if (e.target.closest('[data-all]') && $('#kb-filter').value.trim() === '@me') $('#kb-filter').value = '';
		else return;
		setTimeout(() => { kbTree(); kbRender(); });
	});
}

async function avatarUpload(file) {
	const img = await createImageBitmap(file).catch(() => { throw new Error('that file is not an image this browser can read'); });
	const side = Math.min(img.width, img.height);
	const can  = Object.assign(document.createElement('canvas'), { width: 256, height: 256 });
	can.getContext('2d').drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 256, 256);
	const blob = await new Promise((resolve) => can.toBlob(resolve, 'image/webp', .88));
	const form = new FormData();
	form.append('file', blob, 'avatar.webp');
	const res  = await fetch('/api/me/avatar', { method: 'PUT', body: form });
	const data = await res.json().catch(() => ({}));
	if (!res.ok) throw new Error(data.error || res.statusText);
	KB.me = { ...KB.me, ...data };
	await kbLoad();
	accountLoad();
	toast('Picture updated');
}

function authShow(mode, token) {
	const f = $('#auth-form');
	$('#auth').hidden = false;
	$('#auth').dataset.mode = mode;
	$('#auth').dataset.token = token || '';
	const fresh = mode !== 'login'; // signup and setup both make a new account
	$('#auth-title').textContent = { signup: 'Create your account', setup: 'Create the admin account', login: 'Sign in' }[mode];
	$('#auth-submit').textContent = fresh ? 'Create account' : 'Sign in';
	$('#auth-text').textContent = { signup: 'You were invited to this board. Pick a username (lowercase) and a password of at least 8 characters.', setup: 'This board has no accounts yet. The first one is the admin: pick a username (lowercase) and a password of at least 8 characters.', login: '' }[mode];
	$('#auth-confirm').hidden = !fresh;
	f.elements.password.autocomplete = fresh ? 'new-password' : 'current-password';
	$('#auth-error').hidden = true;
	if (mode === 'signup') fetch(`/api/invites/check/${token}`).then((res) => { if (!res.ok) authError('This invite link is invalid, used or expired. Ask for a new one.'); });
	f.elements.username.focus();
}

function authError(msg) {
	$('#auth-error').textContent = msg;
	$('#auth-error').hidden = false;
}

async function authSubmit() {
	const f      = $('#auth-form').elements;
	const mode   = $('#auth').dataset.mode;
	if (mode !== 'login' && f.password.value !== f.confirm.value) return authError('The passwords do not match.');
	const res  = await fetch(`/api/${mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: f.username.value, password: f.password.value, token: $('#auth').dataset.token }) });
	const data = await res.json().catch(() => ({}));
	if (!res.ok) return authError(data.error || 'Something went wrong.');
	$('#auth-form').reset();
	$('#auth').hidden = true;
	if (mode !== 'login') location.hash = '#/board/all';
	location.reload();
}

async function inboxLoad() {
	inboxRender(await api('GET', '/api/notifications'));
}

function inboxRender(data) {
	KB.me.unread = data.unread;
	meRender();
	const icon = { mention: '@', assign: '→', activity: '•' };
	$('#inbox').innerHTML = `<div class="inbox-wrap">
		<header class="inbox-head"><h2>Inbox</h2><span class="dim">${data.unread} unread</span><span class="spacer"></span>${data.unread ? '<button id="inbox-read">Mark all read</button>' : ''}</header>
		${data.notifications.map((n) => `<a class="note${n.read ? '' : ' unread'}" data-id="${n.id}" href="${n.task_id ? `#/task/${n.task_id}` : '#/inbox'}">
			<span class="note-kind k-${n.kind}">${icon[n.kind] || '•'}</span>
			<span class="note-body"><span class="note-text">${esc(n.text)}</span>${n.title ? `<span class="dim note-title">${esc(n.title)}</span>` : ''}</span>
			<span class="dim note-time">${new Date(n.created).toLocaleString()}</span>
		</a>`).join('') || '<p class="dim">Nothing yet. Mentions, assignments and activity on your tasks show up here.</p>'}
	</div>`;
}

function meRender() {
	const badge = $('#nav-inbox .badge');
	badge.hidden = !KB.me?.unread;
	badge.textContent = KB.me?.unread > 99 ? '99+' : KB.me?.unread || '';
	document.body.classList.toggle('is-admin', !!KB.me?.is_admin);
	$('#user-list').innerHTML = (KB.users || []).map((u) => `<option value="${esc(u.username)}">`).join('');
}

function mentionCheck(ta) {
	const box = ta.closest('.md-editor');
	let pop   = box.querySelector('.mention-pop');
	const m   = ta.value.slice(0, ta.selectionStart).match(/(?:^|[^\w@])@([a-z0-9_-]{0,32})$/i);
	const hit = m ? (KB.users || []).filter((u) => u.username.startsWith(m[1].toLowerCase())).slice(0, 6) : [];
	if (!hit.length) return pop && (pop.hidden = true);
	if (!pop) {
		pop = document.createElement('div');
		pop.className = 'mention-pop';
		box.append(pop);
	}
	pop.dataset.start = ta.selectionStart - m[1].length;
	pop.innerHTML = hit.map((u, i) => `<button type="button" class="mention${i ? '' : ' active'}" data-user="${esc(u.username)}">${avatar(u.username)}${esc(u.username)}</button>`).join('');
	pop.hidden = false;
}

function mentionPick(ta, name) {
	const pop = ta.closest('.md-editor').querySelector('.mention-pop');
	mdInsert(ta, +pop.dataset.start, ta.selectionStart, name + ' ');
	pop.hidden = true;
}

function secretShow(box, text, secret, extra) {
	box.innerHTML = `<p>${esc(text)}</p><input readonly value="${esc(secret)}">${extra ? `<p class="dim small">Claude Code:</p><input readonly value="${esc(extra)}">` : ''}<button type="button" class="link-btn" data-close>Hide</button>`;
	box.hidden = false;
	box.querySelectorAll('input').forEach((i) => i.onclick = () => i.select());
	box.querySelector('input').select();
	box.querySelector('[data-close]').onclick = () => { box.hidden = true; box.innerHTML = ''; };
}

async function shareAdd() {
	const who = $('#share-who').value.trim();
	if (!who) return;
	const body = who.startsWith('team:') ? { team: who.slice(5) } : { username: who.replace(/^@/, '') };
	await guard(async () => { shareRender((await api('POST', `/api/projects/${KB.project}/shares`, { ...body, role: $('#share-role').value })).shares); $('#share-who').value = ''; });
}

async function shareOpen() {
	const p = project(KB.project);
	$('#share-name').textContent = p.path;
	const [shares, teams] = await Promise.all([api('GET', `/api/projects/${p.id}/shares`), api('GET', '/api/teams')]);
	$('#share-targets').innerHTML = KB.users.filter((u) => u.id !== KB.me.id).map((u) => `<option value="${esc(u.username)}">`).join('') + teams.teams.map((t) => `<option value="team:${esc(t.name)}">`).join('');
	shareRender(shares.shares);
	$('#share-dlg').showModal();
}

function shareRender(shares) {
	const roles = ['viewer', 'editor', 'manager'];
	$('#share-list').innerHTML = shares.map((s) => `<div class="share-row${s.inherited ? ' inherited' : ''}">
		${s.kind === 'team' ? '<i class="av team">T</i>' : avatar(s.name)}
		<span class="share-name">${esc(s.name)}${s.kind === 'team' ? ' <small class="dim">team</small>' : ''}${s.inherited ? ` <small class="dim">via ${esc(s.project)}</small>` : ''}</span>
		${s.role === 'owner' ? '<span class="dim">owner</span>' : s.inherited ? `<span class="dim">${s.role}</span>` : `<select data-share-role data-kind="${s.kind}" data-name="${esc(s.name)}">${roles.map((r) => `<option${r === s.role ? ' selected' : ''}>${r}</option>`).join('')}</select><button type="button" class="icon small" data-share-del="${s.id}" aria-label="Remove">${SVG.x}</button>`}
	</div>`).join('') || '<p class="dim">Only you (and admins) can see this project.</p>';
}

function teamsRender() {
	$('#teams').innerHTML = KB.teams.map((t) => `<div class="team" data-id="${t.id}">
		<div class="team-head"><b>${esc(t.name)}</b><span class="dim">${t.members.length} member${t.members.length === 1 ? '' : 's'}</span><span class="spacer"></span>
			${t.members.some((m) => m.id === KB.me.id) ? '<button type="button" class="link-btn" data-team="leave">Leave</button>' : ''}
			${t.owner ? '<button type="button" class="link-btn danger" data-team="delete">Delete</button>' : ''}
		</div>
		<div class="team-members">${t.members.map((m) => `<span class="person">${avatar(m.username)}${esc(m.username)}${m.owner ? ' <small>owner</small>' : ''}${t.owner && m.id !== KB.me.id ? `<button type="button" class="link-btn" data-team="${m.owner ? 'demote' : 'promote'}" data-user="${m.id}">${m.owner ? 'make member' : 'make owner'}</button><button type="button" data-team="remove" data-user="${m.id}" aria-label="Remove">${SVG.x}</button>` : ''}</span>`).join('')}</div>
		${t.owner ? '<form class="inline-form team-add"><input name="username" list="user-list" placeholder="add by username" spellcheck="false"><button>Add</button></form>' : ''}
	</div>`).join('') || '<p class="dim">You are not in any team yet.</p>';
}

function tokensRender(tokens) {
	$('#tokens').innerHTML = tokens.map((t) => `<div class="row-item"><b>${esc(t.name)}</b><span class="dim">created ${new Date(t.created).toLocaleDateString()} · ${t.used ? `last used ${new Date(t.used).toLocaleString()}` : 'never used'}</span><span class="spacer"></span><button type="button" class="link-btn danger" data-token="${t.id}">Revoke</button></div>`).join('') || '<p class="dim">No tokens yet.</p>';
}

async function usersLoad() {
	const [users, invites] = await Promise.all([api('GET', '/api/users'), api('GET', '/api/invites')]);
	const now = new Date().toISOString();
	$('#users').innerHTML = users.users.map((u) => `<div class="row-item">${avatar(u.username)}<b>${esc(u.username)}</b>${u.is_admin ? '<small class="pill">admin</small>' : ''}<span class="dim">joined ${new Date(u.created).toLocaleDateString()}</span><span class="spacer"></span>${u.id === KB.me.id ? '' : `<button type="button" class="link-btn" data-user-act="reset" data-id="${u.id}" data-name="${esc(u.username)}">Reset password</button><button type="button" class="link-btn danger" data-user-act="delete" data-id="${u.id}" data-name="${esc(u.username)}">Delete</button>`}</div>`).join('');
	$('#invites').innerHTML = invites.invites.map((i) => `<div class="row-item"><span>Created ${new Date(i.created).toLocaleString()}</span><span class="dim">${i.used ? `used by ${esc(i.used_by || 'a deleted user')}` : i.expires < now ? 'expired' : `pending, expires ${new Date(i.expires).toLocaleDateString()}`}</span><span class="spacer"></span>${!i.used && i.expires >= now ? `<button type="button" class="link-btn danger" data-invite="${i.id}">Revoke</button>` : ''}</div>`).join('') || '<p class="dim">No invites yet.</p>';
}


/* ---------- markdown editor ---------- */

const MD_TOOLS = [
	['bold', 'Bold (Ctrl+B)', '<b>B</b>'],
	['italic', 'Italic (Ctrl+I)', '<i>I</i>'],
	['heading', 'Heading', '<b>H</b>'],
	['code', 'Code', '<svg viewBox="0 0 24 24"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"/></svg>'],
	['link', 'Link (Ctrl+K)', '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>'],
	['ul', 'Bullet list', '<svg viewBox="0 0 24 24"><path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>'],
	['check', 'Checklist', '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="6" height="6" rx="1"/><path d="m3.5 17 2 2 3.5-4M13 7h8M13 17h8"/></svg>'],
	['quote', 'Quote', '<svg viewBox="0 0 24 24"><path d="M5 6v12M10 8h10M10 12h10M10 16h7"/></svg>'],
	['task', 'Link a task (#123)', '<b>#</b>']
];

function mdEditor(cls, value, placeholder, rows) {
	return `<div class="md-editor">
		<div class="md-bar"><button type="button" class="md-tab on" data-md="write">Write</button><button type="button" class="md-tab" data-md="preview">Preview</button><span class="md-sep"></span>${MD_TOOLS.map(([k, title, icon]) => `<button type="button" class="md-tool" data-md="${k}" title="${title}" aria-label="${title}">${icon}</button>`).join('')}</div>
		<textarea class="md-input ${cls}" rows="${rows}" spellcheck="false" placeholder="${esc(placeholder)}">${esc(value)}</textarea>
		<div class="md-preview markdown" hidden></div>
	</div>`;
}

function mdInsert(ta, start, end, text, selStart, selEnd) {
	ta.focus();
	ta.setSelectionRange(start, end);
	if (!document.execCommand('insertText', false, text)) ta.setRangeText(text, start, end, 'end');
	ta.setSelectionRange(selStart ?? start + text.length, selEnd ?? start + text.length);
}

function mdKey(e) {
	const ta  = e.target;
	const pop = ta.closest('.md-editor')?.querySelector('.mention-pop:not([hidden])');
	if (pop && ['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)) {
		e.preventDefault();
		e.stopPropagation();
		const items = [...pop.querySelectorAll('.mention')];
		const at    = items.findIndex((x) => x.classList.contains('active'));
		if (e.key === 'Escape') return pop.hidden = true;
		if (e.key === 'Enter' || e.key === 'Tab') return mentionPick(ta, items[Math.max(at, 0)].dataset.user);
		const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
		return items.forEach((x, i) => x.classList.toggle('active', i === next));
	}
	if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && ['b', 'i', 'k'].includes(e.key.toLowerCase())) {
		e.preventDefault();
		return mdTool(ta, { b: 'bold', i: 'italic', k: 'link' }[e.key.toLowerCase()]);
	}
	if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || ta.selectionStart !== ta.selectionEnd) return;
	const s     = ta.selectionStart;
	const begin = ta.value.lastIndexOf('\n', s - 1) + 1;
	const line  = ta.value.slice(begin, s);
	const m     = line.match(/^(\s*)([-*] \[[ xX]\] |[-*] |(\d+)\. |> )/);
	if (!m) return;
	e.preventDefault();
	if (line.length === m[0].length) return mdInsert(ta, begin, s, '');
	mdInsert(ta, s, s, '\n' + m[1] + (m[3] ? `${+m[3] + 1}. ` : m[2].replace(/\[[xX]\]/, '[ ]')));
}

async function mdTool(ta, kind) {
	const s = ta.selectionStart, e = ta.selectionEnd, v = ta.value, sel = v.slice(s, e);
	const wrap = (a, b, ph) => { const body = sel || ph; mdInsert(ta, s, e, a + body + b, s + a.length, s + a.length + body.length); };
	if (kind === 'bold') return wrap('**', '**', 'bold');
	if (kind === 'italic') return wrap('_', '_', 'italic');
	if (kind === 'code') return sel.includes('\n') ? wrap('```\n', '\n```', 'code') : wrap('`', '`', 'code');
	if (kind === 'link') { const text = sel || 'text'; const at = s + text.length + 3; return mdInsert(ta, s, e, `[${text}](url)`, at, at + 3); }
	if (kind === 'task') {
		const id = await ask({ title: 'Link a task', text: 'Task number', value: '', ok: 'Insert' });
		const n  = parseInt((id || '').replace('#', ''), 10);
		return n ? mdInsert(ta, s, e, `#${n}`) : ta.focus();
	}
	const prefix = { heading: '## ', ul: '- ', check: '- [ ] ', quote: '> ' }[kind];
	const begin  = v.lastIndexOf('\n', s - 1) + 1;
	const lines  = v.slice(begin, e).split('\n');
	const on     = lines.every((l) => l.startsWith(prefix));
	const out    = lines.map((l) => on ? l.slice(prefix.length) : prefix + l).join('\n');
	mdInsert(ta, begin, e, out, begin, begin + out.length);
}

function mdTab(btn) {
	const box     = btn.closest('.md-editor');
	const ta      = box.querySelector('.md-input');
	const preview = box.querySelector('.md-preview');
	const show    = btn.dataset.md === 'preview';
	box.querySelectorAll('.md-tab').forEach((b) => b.classList.toggle('on', b === btn));
	box.querySelectorAll('.md-tool').forEach((b) => b.disabled = show);
	if (show) {
		preview.style.minHeight = ta.offsetHeight + 'px';
		preview.innerHTML = ta.value.trim() ? mdHtml(ta.value) : '<p class="dim">Nothing to preview</p>';
	}
	ta.hidden = show;
	preview.hidden = !show;
	if (!show) ta.focus();
}


/* ---------- drawer ---------- */

function kbEvent(e) {
	const v = (x) => `<b>${esc(x)}</b>`;
	const who = e.agent ? `${v(e.agent)} <span class="dim">(${esc(e.actor)})</span>` : v(e.actor);
	const link = (x) => { const [k, id] = x.split(':'); return `${KINDS[k] || esc(k)} <a href="${taskHref(+id)}">#${+id}</a>`; };
	const what = {
		created    : () => 'created the task',
		title      : () => `renamed it to ${v(e.new)}`,
		description: () => 'edited the description',
		status     : () => `moved it from ${v(STATUS[e.old] || e.old)} to ${v(STATUS[e.new] || e.new)}`,
		priority   : () => `set priority to ${v(PRIO[+e.new] ?? e.new)}`,
		due        : () => e.new ? `set the due date to ${v(e.new)}` : 'cleared the due date',
		remind_at  : () => e.new ? `set a reminder for ${v(new Date(e.new).toLocaleString())}` : 'cleared the reminder',
		project    : () => `moved it from ${v(e.old)} to ${v(e.new)}`,
		label      : () => e.new ? `added label ${v(e.new)}` : `removed label ${v(e.old)}`,
		assignee   : () => e.new ? `assigned ${v(e.new)}` : `unassigned ${v(e.old)}`,
		link       : () => e.new ? `added link: ${link(e.new)}` : `removed link: ${link(e.old)}`,
		attachment : () => e.new ? `attached ${v(e.new)}` : `removed attachment ${v(e.old)}`,
		comment    : () => 'commented'
	}[e.field];
	return `<li><span>${who} ${what ? what() : esc(e.field)}</span><span class="dim at">${new Date(e.at).toLocaleString()}</span></li>`;
}

function kbDrawer() {
	const t     = KB.task;
	const old   = pane();
	const same  = old.dataset.task === String(t.id);
	const draft = same ? old.querySelector('.comment-input')?.value : '';
	const edit  = same ? old.querySelector('.desc-input')?.value : undefined;
	const page  = KB.view === 'task';
	const opts  = (items, value) => items.map(([v, label]) => `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(label)}</option>`).join('');
	const title = `<textarea class="title" rows="1" data-field="title" spellcheck="false">${esc(t.title)}</textarea>`;
	const fields = `<div class="fields">
				<label>Project<select data-field="project_id">${opts(KB.projects.filter((p) => !p.archived || p.id === t.project_id).map((p) => [p.id, p.path]), t.project_id)}</select></label>
				<label>Status<select data-field="status">${opts(Object.entries(STATUS), t.status)}</select></label>
				<label>Priority<select data-field="priority">${opts(PRIO.map((p, i) => [i, i ? `${i} · ${p}` : p]), t.priority)}</select></label>
				<label>Due<input type="date" data-field="due" value="${esc((t.due || '').slice(0, 10))}"></label>
				<label>Reminder<input type="datetime-local" data-field="remind_at" value="${esc((t.remind_at || '').slice(0, 16))}"></label>
			</div>`;
	const labels = `<section>
				<h4>Labels</h4>
				<div class="chips">
					${t.labels.map((l) => chip(l, `<button data-act="unlabel" data-label="${esc(l)}" aria-label="Remove ${esc(l)}">${SVG.x}</button>`)).join('')}
					<input class="label-input" list="kb-label-list" placeholder="add  ns:value" spellcheck="false">
					<datalist id="kb-label-list">${KB.labels.filter((l) => !t.labels.includes(l.name)).map((l) => `<option value="${esc(l.name)}">`).join('')}</datalist>
				</div>
			</section>`;
	const people = `<section>
				<h4>Assignees</h4>
				<div class="chips">
					${t.assignees.map((a) => `<span class="person">${avatar(a)}${esc(a)}<button data-act="unassign" data-user="${esc(a)}" aria-label="Unassign ${esc(a)}">${SVG.x}</button></span>`).join('')}
					<input class="assign-input" list="user-list" placeholder="add  @user" spellcheck="false">
				</div>
			</section>`;
	const desc = `<section>
				<h4>Description <button class="icon small" data-act="edit-desc" title="Edit description" aria-label="Edit description">${SVG.edit}</button></h4>
				<div class="markdown desc">${t.description.trim() ? mdHtml(t.description) : '<p class="dim">No description</p>'}</div>
			</section>`;
	const files = `<section>
				<h4>Attachments <button class="icon small" data-act="attach" title="Attach files (or drop them here)" aria-label="Attach files">${SVG.plus}</button></h4>
				<input type="file" class="file-input" multiple hidden>
				<ul class="files">${t.attachments.map((a) => `<li data-id="${a.id}" data-name="${esc(a.name)}" data-mime="${esc(a.mime)}">${a.mime.startsWith('image/') ? '<img class="thumb" alt="">' : `<span class="ficon">${SVG.clip}</span>`}<button class="fname link-btn" data-act="open-file" title="Open">${esc(a.name)}</button><span class="dim fsize">${size(a.size)}</span><button class="icon small" data-act="save-file" title="Download" aria-label="Download">${SVG.down}</button><button class="icon small" data-act="del-file" title="Delete" aria-label="Delete">${SVG.x}</button></li>`).join('') || '<li class="dim empty">Drop files here or click +</li>'}</ul>
			</section>`;
	const links = `<section>
				<h4>Links</h4>
				<ul class="links">${t.links.map((l) => `<li class="${l.status === 'done' ? 'done' : ''}"><span class="kind">${KINDS[l.kind] || l.kind}</span><a href="${taskHref(l.id)}">#${l.id} ${esc(l.title)}</a><button class="icon small" data-act="unlink" data-kind="${l.kind}" data-other="${l.id}" aria-label="Remove link">${SVG.x}</button></li>`).join('')}</ul>
				<div class="row"><select class="link-kind">${opts(Object.entries(KINDS), 'related')}</select><div class="link-pick"><input class="link-other" placeholder="#id or title" autocomplete="off" spellcheck="false"><div class="link-sugg" hidden></div></div><button class="icon" data-act="link" title="Add link" aria-label="Add link">${SVG.plus}</button></div>
			</section>`;
	const comments = `<section>
				<h4>Comments</h4>
				<div class="comments">${t.comments.map((c) => `<div class="comment"><div class="who"><b>${esc(c.author || 'unknown')}</b> <span class="dim">${new Date(c.created).toLocaleString()}</span></div><div class="markdown">${mdHtml(c.body)}</div></div>`).join('')}</div>
				${mdEditor('comment-input', '', 'Add a comment  (markdown, Ctrl+Enter to send)', 3)}
				<div class="row end"><button class="primary" data-act="comment">Comment</button></div>
			</section>`;
	const history = t.history.length ? `<section>
				<details class="history"${same && old.querySelector('.history')?.open ? ' open' : ''}>
					<summary><h4>${ICONS.chev}History <span class="dim">${t.history.length}</span></h4></summary>
					<ul class="events">${t.history.map(kbEvent).join('')}</ul>
				</details>
			</section>` : '';
	const stamps = `<p class="stamps dim">Created ${new Date(t.created).toLocaleString()} · updated ${new Date(t.updated).toLocaleString()}${t.done_at ? ` · done ${new Date(t.done_at).toLocaleString()}` : ''}${t.archived ? ' · archived' : ''}</p>`;
	const el = pane();
	if (page) {
		const column = KB.tasks.filter((x) => x.status === t.status);
		if (t.status === 'done') column.sort((a, b) => (b.done_at || '').localeCompare(a.done_at || ''));
		const at   = column.findIndex((x) => x.id === t.id);
		const step = (d, icon, label) => { const x = at < 0 ? null : column[at + d]; return x ? `<a class="icon btn" href="#/task/${x.id}" title="${label}: #${x.id} ${esc(x.title)}" aria-label="${label}">${icon}</a>` : `<span class="icon btn off">${icon}</span>`; };
		const c    = tint(t.project_id);
		el.innerHTML = `<div class="tp">
			<header class="tp-head">
				<a class="icon btn" href="${kbBase()}" title="Back to the board (Esc)" aria-label="Back to the board">${SVG.back}</a>
				<span class="id">#${t.id}</span>
				<a class="proj" href="#/board/${t.project_id}">${c ? `<i class="dot" style="--c:${c}"></i>` : ''}${esc(t.project)}</a>
				<span class="st st-${t.status}">${STATUS[t.status]}</span>
				<span class="spacer"></span>
				${at >= 0 ? `<span class="dim pos">${at + 1} / ${column.length} in ${STATUS[t.status]}</span>` : ''}
				${step(-1, SVG.prev, 'Previous')}${step(1, SVG.next, 'Next')}
				<button class="icon" data-act="panel" title="Open in the side panel" aria-label="Open in the side panel">${SVG.panel}</button>
				<button class="icon danger" data-act="delete" title="Delete task" aria-label="Delete task">${SVG.trash}</button>
			</header>
			${title}
			<div class="tp-grid">
				<div class="tp-main">${desc}${files}${comments}${history}</div>
				<aside class="tp-side">${fields}${people}${labels}${links}${stamps}</aside>
			</div>
		</div>`;
	} else {
		el.innerHTML = `
		<header>
			<span class="id">#${t.id}</span>
			<span class="spacer"></span>
			<button class="icon" data-act="expand" title="Open as a full page" aria-label="Open as a full page">${SVG.expand}</button>
			<button class="icon danger" data-act="delete" title="Delete task" aria-label="Delete task">${SVG.trash}</button>
			<button class="icon" data-act="close" title="Close (Esc)" aria-label="Close">${SVG.x}</button>
		</header>
		<div class="body">${title}${fields}${people}${labels}${desc}${files}${links}${comments}${history}${stamps}</div>`;
		el.hidden = false;
		document.querySelectorAll('.card').forEach((card) => card.classList.toggle('active', +card.dataset.id === t.id));
	}
	el.classList.toggle('readonly', t.role < 2);
	if (t.role < 2) {
		el.querySelectorAll('input, select, textarea').forEach((x) => x.disabled = true);
		el.querySelectorAll('[data-act]').forEach((x) => x.hidden = !['close', 'expand', 'panel', 'open-file', 'save-file'].includes(x.dataset.act));
		el.querySelectorAll('.md-editor, .assign-input, .label-input, .row').forEach((x) => x.hidden = true);
	} else {
		el.querySelectorAll('.desc input[type=checkbox]').forEach((box) => box.disabled = false);
	}
	if (KB.thumbsFor !== t.id) thumbsFree();
	KB.thumbsFor = t.id;
	el.querySelectorAll('.files li[data-mime^="image/"]').forEach((li) => {
		if (!KB.thumbs.has(li.dataset.id)) KB.thumbs.set(li.dataset.id, fileBlob(li.dataset.id));
		KB.thumbs.get(li.dataset.id).then((url) => li.querySelector('.thumb').src = url).catch(() => KB.thumbs.delete(li.dataset.id));
	});
	el.dataset.task = t.id;
	el.setAttribute('aria-label', `Task #${t.id}: ${t.title}`);
	if (!same && !el.contains(document.activeElement)) el.focus({ preventScroll: true }); // keyboard focus moves into a newly opened task
	if (draft) el.querySelector('.comment-input').value = draft;
	if (edit !== undefined && t.role >= 2) { kbAction('edit-desc'); el.querySelector('.desc-input').value = edit; }
}

function kbClose() {
	const id = KB.task?.id;
	const back = id && !$('#drawer').hidden && $('#drawer').contains(document.activeElement) ? document.querySelector(`#board .card[data-id="${id}"]`) : null;
	KB.task = null;
	$('#drawer').hidden = true;
	thumbsFree();
	delete $('#drawer').dataset.task; // reopening starts fresh
	if (back) { back.tabIndex = -1; back.focus({ preventScroll: true }); } // focus returns to the card it was opened from
	document.querySelectorAll('.card.active').forEach((c) => c.classList.remove('active'));
}

async function kbPatch(fields) {
	const id  = KB.task.id;
	const seq = ++KB.patchSeq;
	Object.assign(KB.task, fields); // the next quick edit builds on this one
	await guard(async () => {
		let saved;
		try {
			saved = await api('PATCH', `/api/tasks/${id}`, fields);
		} catch (err) {
			if (KB.task?.id === id) { KB.task = await api('GET', `/api/tasks/${id}`); kbDrawer(); }
			throw err;
		}
		if (seq === KB.patchSeq && KB.task?.id === id) await kbTaskSaved(saved);
	});
}

async function kbAction(act, el) {
	const t = KB.task;
	if (act === 'close') return location.hash = kbBase();
	if (act === 'expand') return location.hash = `#/task/${t.id}`;
	if (act === 'panel') return location.hash = `#/board/${KB.project ?? 'all'}/${t.id}`;
	if (act === 'unlabel') return kbPatch({ labels: t.labels.filter((l) => l !== el.dataset.label) });
	if (act === 'unassign') return kbPatch({ assignees: t.assignees.filter((a) => a !== el.dataset.user) });
	if (act === 'unlink') return guard(async () => kbTaskSaved(await api('DELETE', `/api/tasks/${t.id}/links?kind=${el.dataset.kind}&other=${el.dataset.other}`)));
	if (act === 'link') {
		const other = parseInt(pane().querySelector('.link-other').value.replace('#', ''), 10);
		if (!other) return toast('Enter a task id', true);
		return guard(async () => kbTaskSaved(await api('POST', `/api/tasks/${t.id}/links`, { kind: pane().querySelector('.link-kind').value, other })));
	}
	if (act === 'comment') {
		const input = pane().querySelector('.comment-input');
		const body  = input.value;
		if (!body.trim() || KB.commenting) return;
		KB.commenting = true;
		return guard(async () => { await api('POST', `/api/tasks/${t.id}/comments`, { body }).then((saved) => { input.value = ''; return kbTaskSaved(saved); }); }).finally(() => { KB.commenting = false; });
	}
	if (act === 'delete') {
		if (!await ask({ title: 'Delete task?', text: `#${t.id} ${t.title}\nThis cannot be undone.`, ok: 'Delete', danger: true })) return;
		return guard(async () => {
			await api('DELETE', `/api/tasks/${t.id}`);
			location.hash = kbBase();
			await kbLoad();
			toast(`Deleted #${t.id}`);
		});
	}
	if (act === 'edit-desc') {
		const box = pane().querySelector('.desc');
		if (!box) return pane().querySelector('.desc-input')?.focus(); // already editing
		box.outerHTML = `<div class="desc-edit">${mdEditor('desc-input', t.description, 'Markdown. Steps as "- [ ] ".', 8)}<div class="row end"><button data-act="cancel-desc">Cancel</button><button class="primary" data-act="save-desc">Save</button></div></div>`;
		const input = pane().querySelector('.desc-input');
		input.style.height = Math.max(160, input.scrollHeight + 4) + 'px';
		return input.focus();
	}
	if (act === 'attach') return pane().querySelector('.file-input').click();
	if (act === 'open-file' || act === 'save-file') {
		const li = el.closest('li');
		return guard(async () => {
			const url = await fileBlob(li.dataset.id);
			setTimeout(() => URL.revokeObjectURL(url), 60_000); // long enough for the tab or download to read it
			if (act === 'open-file' && /^(image\/(png|jpeg|gif|webp|avif)|application\/pdf|text\/plain)$/.test(li.dataset.mime)) return window.open(url, '_blank');
			Object.assign(document.createElement('a'), { href: url, download: li.dataset.name }).click();
		});
	}
	if (act === 'del-file') {
		const li = el.closest('li');
		if (!await ask({ title: `Delete ${li.dataset.name}?`, text: 'This cannot be undone.', ok: 'Delete', danger: true })) return;
		return guard(async () => kbTaskSaved(await api('DELETE', `/api/attachments/${li.dataset.id}`)));
	}
	if (act === 'cancel-desc') return kbDrawer();
	if (act === 'save-desc') return kbPatch({ description: pane().querySelector('.desc-input').value });
}


/* ---------- routing ---------- */

function kbLost() {
	toast('That page does not exist', true);
	location.replace('#/board/all');
}

async function kbRoute(hash) {
	const seq = ++KB.routeSeq;
	const [, kind, a, b] = hash.match(/^#\/(board|calendar|labels|search|settings|task|inbox)\/?([^/]*)\/?([^/]*)/) || [];
	if (kind === 'task' ? !/^\d+$/.test(a) : ['board', 'calendar'].includes(kind) && !(/^(all|\d+)?$/.test(a) && /^\d*$/.test(b))) return kbLost();
	if (kind === 'labels') return location.replace('#/settings');
	if (['search', 'settings', 'inbox'].includes(kind)) {
		if (KB.view !== kind) KB.setLoaded = false;
		KB.view = kind;
		kbClose();
		if (KB.project === undefined) KB.project = null;
		await kbLoad();
		if (kind === 'search') { $('#search-q').focus(); guard(searchRun); }
		return store('last', hash);
	}
	if (kind === 'task') {
		const t = await api('GET', `/api/tasks/${parseInt(a, 10)}`);
		if (seq !== KB.routeSeq) return;
		KB.task = t;
		KB.view = 'task';
		if (KB.project === undefined) KB.project = null;
		$('#drawer').hidden = true;
		await kbLoad();
		kbDrawer();
		$('#taskpage').scrollTop = 0;
		return store('last', hash);
	}
	const pid  = a && a !== 'all' ? parseInt(a, 10) : null;
	const view = kind;
	if (pid !== KB.project || view !== KB.view) {
		KB.view      = view;
		KB.project   = pid;
		KB.doneLimit = 25;
		await kbLoad();
	} else {
		kbBar();
	}
	if (b) {
		const t = await api('GET', `/api/tasks/${b}`);
		if (seq !== KB.routeSeq) return;
		KB.task = t;
		kbDrawer();
	} else {
		kbClose();
	}
	store('last', hash);
}


/* ---------- events ---------- */

function kbBind() {
	$('#kb-filter').addEventListener('input', () => { kbTree(); kbRender(); });
	dndBind();
	$('#search-q').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => guard(searchRun), 200); });
	$('#search-filters').addEventListener('change', () => guard(searchRun));
	$('#search-filters').addEventListener('submit', (e) => e.preventDefault());
	$('#search-filters').addEventListener('reset', () => setTimeout(() => guard(searchRun)));
	$('#settings-form').addEventListener('submit', (e) => { e.preventDefault(); guard(setSave); });
	$('#set-clear').onclick = () => { const f = $('#settings-form'); f.elements.clear_token.value = '1'; f.elements.ntfy_token.value = ''; f.elements.ntfy_token.placeholder = 'cleared on save'; $('#set-clear').hidden = true; };
	$('#settings-test').onclick = () => guard(async () => { await api('POST', '/api/settings/test'); toast('Test push sent'); });
	$('#kb-desc').addEventListener('click', (e) => {
		const box = $('#kb-desc');
		const act = e.target.closest('[data-desc]')?.dataset.desc;
		const p   = project(KB.project);
		if (!act || !p) return;
		if (act === 'toggle') { store('descCollapsed', !store('descCollapsed')); return descRender(p); }
		if (act === 'edit') return descRender(p, true);
		if (act === 'cancel') return descRender(p);
		if (act === 'color') return box.querySelectorAll('.swatch').forEach((el) => el.classList.toggle('on', el === e.target.closest('.swatch')));
		if (act === 'save') guard(async () => { await api('PATCH', `/api/projects/${p.id}`, { description: box.querySelector('textarea').value, folder: box.querySelector('.folder-input').value, color: box.querySelector('.swatch.on')?.dataset.color ?? p.color ?? '' }); await kbLoad(); toast('Project saved'); });
	});
	$('#cal-prev').onclick  = () => kbMonth(-1);
	$('#cal-next').onclick  = () => kbMonth(1);
	$('#cal-today').onclick = () => kbMonth(0);
	$('#lbl-new').onclick   = () => lblNew(false);
	$('#lbl-new-global').onclick = () => lblNew(true);
	$('#cal-grid').addEventListener('click', (e) => {
		const ev = e.target.closest('.ev');
		if (ev) location.hash = taskHref(ev.dataset.id);
	});
	$('#labels').addEventListener('click', (e) => {
		const btn = e.target.closest('[data-act]');
		if (!btn) return;
		if (btn.dataset.act === 'lcolor') e.stopPropagation();
		lblAction(btn.dataset.act, btn);
	});
	$('#lbl-palette').onclick = async (e) => {
		const sw = e.target.closest('.swatch');
		if (!sw) return;
		$('#lbl-palette').hidden = true;
		await guard(async () => { await api('PATCH', '/api/labels', { name: $('#lbl-palette').dataset.name, color: sw.dataset.color }); await kbLoad(); });
	};
	document.addEventListener('click', (e) => { if (!e.target.closest('#lbl-palette')) $('#lbl-palette').hidden = true; });
	$('#kb-filter').addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.target.value = ''; kbTree(); kbRender(); e.target.blur(); } });
	$('#kb-new-task').onclick = () => kbNewTask();
	$('#kb-new-project').onclick = async () => {
		const base = project(KB.project);
		const path = await ask({ title: 'New project', text: 'Path, like Code/AI/hermes. Missing parents are created.', value: base ? base.path + '/' : '', ok: 'Create', path: true });
		if (path) await guard(async () => { const p = await api('POST', '/api/projects', { path }); location.hash = `#/board/${p.id}`; KB.project = undefined; });
	};
	$('#kb-rename').onclick = async () => {
		const p = project(KB.project);
		const path = await ask({ title: 'Rename / move project', value: p.path, ok: 'Save', path: true });
		if (path && path !== p.path) await guard(async () => { await api('PATCH', `/api/projects/${p.id}`, { path }); await kbLoad(); });
	};
	$('#kb-archive').onclick = async () => {
		const p = project(KB.project);
		await guard(async () => { await api('PATCH', `/api/projects/${p.id}`, { archived: !p.archived }); await kbLoad(); toast(p.archived ? 'Unarchived' : 'Archived'); });
	};
	$('#kb-delete').onclick = async () => {
		const p = project(KB.project);
		if (!await ask({ title: 'Delete project?', text: `${p.path}\nOnly empty projects (no tasks, no sub-projects) can be deleted.`, ok: 'Delete', danger: true })) return;
		await guard(async () => { await api('DELETE', `/api/projects/${p.id}`); location.hash = '#/board/all'; });
	};

	$('#kb-tree').addEventListener('click', (e) => {
		const chev = e.target.closest('.chev-btn');
		if (e.target.closest('#kb-archived')) { KB.archived = !KB.archived; return kbTree(); }
		if (!chev) return $('#app').classList.remove('open');
		e.preventDefault();
		KB.collapsed.has(chev.dataset.path) ? KB.collapsed.delete(chev.dataset.path) : KB.collapsed.add(chev.dataset.path);
		store('kbCollapsed', [...KB.collapsed]);
		kbTree();
	});

	document.querySelectorAll('.column').forEach((col) => {
		const status = col.dataset.status;
		col.querySelector('.add').onclick = () => kbNewTask(status);
		new Sortable(col.querySelector('.cards'), {
			group: 'kb', animation: 150, sort: status !== 'done', delay: 150, delayOnTouchOnly: true, ghostClass: 'ghost',
			onStart: (evt) => { KB.dragTask = +evt.item.dataset.id; },
			onEnd: (evt) => {
				setTimeout(() => { KB.dragTask = null; }, 0);
				if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
				const to = evt.to.closest('.column').dataset.status;
				guard(async () => {
					await api('POST', '/api/reorder', { status: to, ids: [...evt.to.children].map((c) => +c.dataset.id) });
					await kbLoad();
					if (KB.task) {
						KB.task = await api('GET', `/api/tasks/${KB.task.id}`);
						kbDrawer();
					}
				});
			}
		});
	});
	$('.column[data-status="done"] .more').onclick = () => { KB.doneLimit += 50; kbBoard(); };
	$('#board').addEventListener('click', (e) => {
		const card = e.target.closest('.card');
		if (!card || e.target.closest('.open-new')) return;
		if (!KB.selecting) return location.hash = taskHref(card.dataset.id);
		const id = +card.dataset.id;
		KB.picked.has(id) ? KB.picked.delete(id) : KB.picked.add(id);
		card.classList.toggle('picked', KB.picked.has(id));
		bulkBar();
	});

	paneBind($('#drawer'));
	paneBind($('#taskpage'));
	$('#kb-select').onclick = () => bulkMode(!KB.selecting);
	$('#bulk-close').onclick = () => bulkMode(false);
	$('#bulk-all').onclick = () => { document.querySelectorAll('#board .card').forEach((c) => KB.picked.add(+c.dataset.id)); kbBoard(); };
	$('#bulk-status').onchange = (e) => bulkApply({ status: e.target.value }, `Moved to ${STATUS[e.target.value]}`);
	$('#bulk-project').onchange = (e) => bulkApply({ project_id: +e.target.value }, `Moved to ${project(+e.target.value)?.path}`);
	$('#bulk-unlabel').onchange = (e) => bulkApply({ remove_label: e.target.value }, `Removed ${e.target.value}`);
	$('#bulk-label').onkeydown = (e) => { if (e.key === 'Enter' && e.target.value.trim()) bulkApply({ add_label: e.target.value.trim() }, `Added ${e.target.value.trim()}`); };
	$('#bulk-label').onchange = (e) => { if (KB.labels.some((l) => l.name === e.target.value.trim())) bulkApply({ add_label: e.target.value.trim() }, `Added ${e.target.value.trim()}`); };
	$('#bulk-delete').onclick = async () => {
		const n = KB.picked.size;
		if (!n || !await ask({ title: `Delete ${n} task${n === 1 ? '' : 's'}?`, text: 'Their comments, links and attachments go too. This cannot be undone.', ok: 'Delete', danger: true })) return;
		await bulkApply({ delete: true }, `Deleted ${n}`);
		KB.picked.clear();
		kbBoard();
	};
	$('#board-form').addEventListener('submit', (e) => { e.preventDefault(); guard(async () => { await api('PUT', '/api/settings', { archive_days: e.target.elements.archive_days.value }); await kbLoad(); toast('Board settings saved'); }); });
}

function taskBoxes(md) {
	// Offset of the character inside each task item's "[ ]", in the order marked renders the checkboxes; null where it cannot be placed
	const out  = [];
	const walk = (tokens, text, base) => {
		let cur = 0;
		for (const tok of tokens || []) {
			const at  = base === null ? -1 : text.indexOf(tok.raw, cur);
			const pos = at < 0 ? null : base + at;
			if (at >= 0) cur = at + tok.raw.length;
			if (tok.type === 'list') walk(tok.items, tok.raw, pos);
			else if (tok.type === 'list_item') {
				const m = tok.task && tok.raw.match(/^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]/);
				if (tok.task) out.push(pos !== null && m ? pos + m[0].length - 2 : null);
				walk(tok.tokens, tok.raw, pos);
			} else if (tok.type === 'blockquote') walk(tok.tokens, tok.raw, null); // quoted text is re-indented; count its boxes without placing them
		}
	};
	walk(marked.lexer(md), md, 0);
	return out;
}

function paneBind(drawer) {
	drawer.addEventListener('click', (e) => {
		const btn = e.target.closest('[data-act]');
		if (btn) return kbAction(btn.dataset.act, btn);
		const box = e.target.closest('.desc input[type=checkbox]');
		if (!box) return;
		e.preventDefault();
		const index = [...drawer.querySelectorAll('.desc li > input[type=checkbox], .desc li > p:first-child > input[type=checkbox]')].indexOf(box);
		const at    = taskBoxes(KB.task.description)[index];
		if (at == null) return;
		const text  = KB.task.description;
		kbPatch({ description: text.slice(0, at) + (text[at] === ' ' ? 'x' : ' ') + text.slice(at + 1) });
	});
	drawer.addEventListener('change', (e) => {
		const el = e.target;
		if (el.matches('.label-input')) {
			const name = el.value.trim().toLowerCase();
			if (name) kbPatch({ labels: [...KB.task.labels, name] });
			return;
		}
		if (el.matches('.assign-input')) {
			const name = el.value.trim().toLowerCase().replace(/^@/, '');
			if (name) kbPatch({ assignees: [...KB.task.assignees, name] });
			return;
		}
		const field = el.dataset.field;
		if (!field) return;
		const value = ['project_id', 'priority'].includes(field) ? parseInt(el.value, 10) : el.value;
		if (field === 'title' && !value.trim()) return kbDrawer();
		kbPatch({ [field]: value });
	});
	drawer.addEventListener('keydown', (e) => {
		if (e.target.matches('.title') && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
		if (e.target.matches('.assign-input') && e.key === 'Enter') { e.preventDefault(); e.target.dispatchEvent(new Event('change', { bubbles: true })); }
		if (e.target.matches('.label-input') && e.key === 'Enter') { e.preventDefault(); e.target.dispatchEvent(new Event('change', { bubbles: true })); }
		if (e.target.matches('.comment-input') && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) kbAction('comment');
		if (e.target.matches('.link-other')) linkKey(e);
	});
	drawer.addEventListener('input', (e) => { if (e.target.matches('.link-other')) linkSuggest(); });
	drawer.addEventListener('change', (e) => { if (e.target.matches('.file-input') && e.target.files.length) fileUpload(e.target.files); });
	drawer.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); drawer.classList.add('drop-files'); } });
	drawer.addEventListener('dragleave', (e) => { if (!drawer.contains(e.relatedTarget)) drawer.classList.remove('drop-files'); });
	drawer.addEventListener('drop', (e) => {
		if (!e.dataTransfer.files.length) return;
		e.preventDefault();
		drawer.classList.remove('drop-files');
		fileUpload(e.dataTransfer.files);
	});
	drawer.addEventListener('focusin', (e) => { if (e.target.matches('.link-other')) guard(async () => { KB.allTasks = (await api('GET', '/api/tasks')).tasks; linkSuggest(); }); });
	drawer.addEventListener('focusout', (e) => { if (e.target.matches('.link-other')) drawer.querySelector('.link-sugg').hidden = true; });
	drawer.addEventListener('mousedown', (e) => {
		const item = e.target.closest('.sugg');
		if (!item) return;
		e.preventDefault();
		linkPick(item);
	});
}

function linkKey(e) {
	const box   = pane().querySelector('.link-sugg');
	const items = [...box.querySelectorAll('.sugg')];
	const at    = items.findIndex((el) => el.classList.contains('active'));
	if (box.hidden || !items.length) {
		if (e.key === 'Enter') kbAction('link');
		return;
	}
	if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
		e.preventDefault();
		const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
		items.forEach((el, i) => el.classList.toggle('active', i === next));
		items[next].scrollIntoView({ block: 'nearest' });
	}
	else if (e.key === 'Enter') { e.preventDefault(); linkPick(items[Math.max(at, 0)]); }
	else if (e.key === 'Escape') { e.stopPropagation(); box.hidden = true; }
}

function linkPick(item) {
	const input = pane().querySelector('.link-other');
	input.value = `#${item.dataset.id} ${item.dataset.title}`;
	pane().querySelector('.link-sugg').hidden = true;
	input.focus();
}

function linkSuggest() {
	const input = pane().querySelector('.link-other');
	const box   = pane().querySelector('.link-sugg');
	const q     = input.value.trim().toLowerCase().replace(/^#/, '');
	if (!q || !KB.task || !KB.allTasks) return box.hidden = true;
	const skip  = new Set([KB.task.id, ...KB.task.links.map((l) => l.id)]);
	const words = q.split(/\s+/);
	const rank  = (t) => {
		const id = String(t.id), title = t.title.toLowerCase();
		if (id === q) return 0;
		if (/^\d+$/.test(q) && id.startsWith(q)) return 1;
		if (title.startsWith(q)) return 2;
		if (words.every((w) => `${id} ${title} ${t.project.toLowerCase()}`.includes(w))) return 3;
		return -1;
	};
	const hits = KB.allTasks.filter((t) => !skip.has(t.id)).map((t) => [rank(t), t]).filter(([r]) => r >= 0).sort((a, b) => a[0] - b[0] || (a[1].status === 'done') - (b[1].status === 'done') || b[1].id - a[1].id).slice(0, 8);
	box.innerHTML = hits.map(([, t], i) => {
		const c = tint(t.project_id);
		return `<button type="button" class="sugg${i ? '' : ' active'} ${t.status}" data-id="${t.id}" data-title="${esc(t.title)}">
			<span class="id">#${t.id}</span>
			<span class="sugg-text"><span class="title">${esc(t.title)}</span><span class="proj">${c ? `<i class="dot" style="--c:${c}"></i>` : ''}${esc(t.project)}</span></span>
			<span class="st st-${t.status}">${STATUS[t.status]}</span>
		</button>`;
	}).join('') || '<p class="dim">No matching tasks</p>';
	box.hidden = false;
}

function kbKey(e) {
	if (e.key === 'Escape' && KB.task && !document.querySelector('dialog[open]') && e.target.closest?.('#drawer, #taskpage') && e.target.matches('input, textarea, select')) {
		e.target.blur(); // first Esc leaves the field, the next one closes the task
		return pane().focus({ preventScroll: true });
	}
	const typing = e.target.closest('input, textarea, select') || document.querySelector('dialog[open]');
	if (e.key === 'Escape' && !typing && KB.task) return location.hash = kbBase();
	if (e.key === 'Escape' && !typing && KB.selecting) return bulkMode(false);
	if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
	if (e.key === '/') { e.preventDefault(); (KB.view === 'search' ? $('#search-q') : $('#kb-filter')).focus(); }
	else if (e.key === 'n') { e.preventDefault(); KB.view === 'settings' ? lblNew() : kbNewTask(); }
}


/* ---------- boot ---------- */

document.addEventListener('DOMContentLoaded', () => {
	if (store('collapsed')) $('#app').classList.add('collapsed');
	marked.setOptions({ gfm: true });
	kbBind();
	$('#toggle').onclick = () => {
		if (narrow()) return $('#app').classList.toggle('open');
		$('#app').classList.toggle('collapsed');
		store('collapsed', $('#app').classList.contains('collapsed'));
	};
	$('#scrim').onclick      = () => $('#app').classList.remove('open');
	$('#dlg-cancel').onclick = () => $('#dialog').close();
	grip($('#side-grip'), '--side', $('#app'), (e) => Math.min(Math.max(e.clientX, 180), innerWidth * .6) + 'px');
	document.addEventListener('keydown', kbKey);
	document.addEventListener('keydown', (e) => { if (e.target.matches?.('.md-input')) mdKey(e); });
	document.addEventListener('mousedown', (e) => { if (e.target.closest('.md-tool')) e.preventDefault(); });
	document.addEventListener('click', (e) => {
		const btn = e.target.closest('[data-md]');
		if (!btn) return;
		if (btn.classList.contains('md-tab')) return mdTab(btn);
		mdTool(btn.closest('.md-editor').querySelector('.md-input'), btn.dataset.md);
	});
	document.addEventListener('input', (e) => { if (e.target.matches?.('.md-input')) mentionCheck(e.target); });
	document.addEventListener('mousedown', (e) => {
		const item = e.target.closest('.mention');
		if (!item) return;
		e.preventDefault();
		mentionPick(item.closest('.md-editor').querySelector('.md-input'), item.dataset.user);
	});
	document.addEventListener('focusout', (e) => { if (e.target.matches?.('.md-input')) setTimeout(() => e.target.closest('.md-editor')?.querySelectorAll('.mention-pop').forEach((p) => p.hidden = true), 150); });
	accountBind();
	const route = () => {
		const invite = location.hash.match(/^#\/invite\/([\w-]+)/);
		if (invite) return authShow('signup', invite[1]);
		if (!KB.me) return;
		if (!/^#\/(board|calendar|labels|search|settings|task|inbox)(\/|$)/.test(location.hash)) return location.replace(store('last') || '#/board/all');
		$('#app').classList.remove('open');
		kbRoute(location.hash).catch((err) => [403, 404].includes(err.status) ? kbLost() : toast(err.message, true));
	};
	window.addEventListener('hashchange', route);
	KB.route = route;
	fetch('/api/me').then((res) => res.ok ? res.json() : null).then((me) => {
		KB.me = me;
		if (me || /^#\/invite\//.test(location.hash)) return route();
		fetch('/api/setup').then((res) => res.json()).then((data) => authShow(data.needed ? 'setup' : 'login'), () => authShow('login'));
	});
});
