// kanopy site - Developed by acidvegas (https://github.com/acidvegas)

const COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const DONE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';

// nav: border once scrolled, mobile menu
const nav = document.querySelector('.nav');
const onScroll = () => nav.classList.toggle('scrolled', scrollY > 8);
addEventListener('scroll', onScroll, {passive: true});
onScroll();
document.querySelector('.menu')?.addEventListener('click', () => nav.classList.toggle('open'));
document.querySelectorAll('.nav-links a').forEach(a => a.addEventListener('click', () => nav.classList.remove('open')));

// shell syntax highlighting for command examples
const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[c]);
function shell(src) {
	const re = /(#[^\n]*)|("(?:[^"\\]|\\.)*"|'[^']*')|(\$\([^)]*\)|\$\{?\w+\}?)|(\s--?[\w-]+(?:=\S*)?)|(&&|\|\||\\$|[|>])|([^\s"'$&|>\\#]+)/gm;
	let out = '', last = 0, first = true;
	for (const m of src.matchAll(re)) {
		out += esc(src.slice(last, m.index)); last = m.index + m[0].length;
		const [t, com, str, vr, flag, op, word] = m;
		if (com) { out += `<span class="sh-c">${esc(t)}</span>`; continue; }
		if (str) { out += `<span class="sh-s">${esc(t).replace(/\$\(?[\w-]+\)?|\$\([^)]*\)/g, v => `<span class="sh-v">${v}</span>`)}</span>`; first = false; continue; }
		if (vr) { out += `<span class="sh-v">${esc(t)}</span>`; first = false; continue; }
		if (flag) { out += `<span class="sh-f">${esc(t)}</span>`; continue; }
		if (op) { out += `<span class="sh-o">${esc(t)}</span>`; if (t !== '\\') first = true; continue; }
		if (word) { out += first ? `<span class="sh-k">${esc(t)}</span>` : esc(t); first = false; }
	}
	out += esc(src.slice(last));
	return out.split('\n').map((line, i, all) => line).join('\n');
}
// a new line starts a new command unless the previous one ended with a backslash
function shellBlock(src) { return src.split(/(?<!\\)\n/).map(shell).join('\n'); }
document.querySelectorAll('.term pre code, .cmd code, pre.mini.sh, .prose pre code.language-sh, .prose pre code.language-bash').forEach(el => {
	el.innerHTML = shellBlock(el.textContent);
});

// copy buttons: on .cmd rows, install blocks and every docs code block
function addCopy(host, text) {
	const b = document.createElement('button');
	b.className = 'copy'; b.type = 'button'; b.title = 'Copy'; b.setAttribute('aria-label', 'Copy to clipboard'); b.innerHTML = COPY;
	b.addEventListener('click', async () => {
		const t = text();
		try { await navigator.clipboard.writeText(t); }
		catch { // the Clipboard API only exists on https; fall back for plain http
			const ta = document.createElement('textarea');
			ta.value = t; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;opacity:0';
			document.body.appendChild(ta); ta.select();
			const ok = document.execCommand('copy'); ta.remove();
			if (!ok) return;
		}
		b.classList.add('done'); b.innerHTML = DONE;
		setTimeout(() => { b.classList.remove('done'); b.innerHTML = COPY; }, 1400);
	});
	host.appendChild(b);
}
document.querySelectorAll('.cmd').forEach(c => addCopy(c, () => c.querySelector('code').textContent));
document.querySelectorAll('.term').forEach(t => addCopy(t.querySelector('.term-bar'), () => t.querySelector('pre.on code').textContent));
document.querySelectorAll('.prose pre').forEach(c => addCopy(c, () => c.querySelector('code').textContent.replace(/\n$/, '')));

// tab groups
document.querySelectorAll('[role="tablist"]').forEach(list => {
	const tabs = [...list.querySelectorAll('[role="tab"]')];
	const pick = tab => tabs.forEach(t => {
		const on = t === tab;
		t.setAttribute('aria-selected', on);
		t.tabIndex = on ? 0 : -1;
		document.getElementById(t.getAttribute('aria-controls')).classList.toggle('on', on);
	});
	tabs.forEach((t, i) => {
		t.addEventListener('click', () => pick(t));
		t.addEventListener('keydown', e => {
			const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
			if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; pick(n); n.focus(); }
		});
	});
});

// video: click to play with sound
document.querySelectorAll('.player').forEach(p => {
	const v = p.querySelector('video');
	p.querySelector('.play').addEventListener('click', () => { p.classList.add('playing'); v.controls = true; v.play(); });
	v.addEventListener('ended', () => { p.classList.remove('playing'); v.controls = false; v.load(); });
});


// reveal on scroll
const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), {rootMargin: '0px 0px -8% 0px'});
document.querySelectorAll('.reveal').forEach(el => io.observe(el));

// docs: highlight the section in view
const toc = document.querySelectorAll('.toc a');
if (toc.length) {
	const map = new Map([...toc].map(a => [a.getAttribute('href').slice(1), a]));
	const heads = [...document.querySelectorAll('.prose h2[id], .prose h3[id]')];
	const spy = () => {
		let cur = heads[0];
		for (const h of heads) if (h.getBoundingClientRect().top < 120) cur = h;
		toc.forEach(a => a.classList.remove('on'));
		map.get(cur?.id)?.classList.add('on');
	};
	addEventListener('scroll', spy, {passive: true});
	spy();
}

// themes tab: cycle the theme screenshots every second while the tab is open
const car = document.querySelector('.carousel');
if (car) {
	const imgs = [...car.querySelectorAll('.slides img')], panel = car.closest('.shot');
	let cur = 0;
	setInterval(() => {
		if (!panel.classList.contains('on') || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		imgs[cur].classList.remove('on');
		cur = (cur + 1) % imgs.length;
		imgs[cur].classList.add('on');
	}, 1000);
}
