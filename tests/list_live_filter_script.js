const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...template.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const ours = blocks.filter(b => b.indexOf('liveFilterDebounce') !== -1);
assert.strictEqual(ours.length, 1,
    'ожидался ровно один блок с живым фильтром, получено ' + ours.length);
const block = ours[0];

function makeEl(tag, attrs) {
    const el = {
        tag,
        attrs: Object.assign({}, attrs || {}),
        children: [],
        value: '',
        _handlers: {},
        submitted: 0,
    };
    el.setAttribute = (key, raw) => { el.attrs[key] = String(raw); };
    el.getAttribute = (key) => (key in el.attrs ? el.attrs[key] : null);
    el.removeAttribute = (key) => { delete el.attrs[key]; };
    el.addEventListener = (type, fn) => {
        (el._handlers[type] = el._handlers[type] || []).push(fn);
    };
    el.appendChild = (child) => { child.parentNode = el; el.children.push(child); return child; };
    el.querySelector = (sel) => {
        for (const child of el.children) {
            if (matches(child, sel)) return child;
            const deep = child.querySelector(sel);
            if (deep) return deep;
        }
        return null;
    };
    el.requestSubmit = () => { el.submitted += 1; };
    return el;
}

function matches(el, sel) {
    const cls = sel.match(/^\.([\w-]+)$/);
    if (cls) return String(el.className || '').split(/\s+/).indexOf(cls[1]) !== -1;
    const attr = sel.match(/^\[([\w-]+)=([^\]]+)\]$/);
    if (attr) return el.getAttribute(attr[1]) === attr[2];
    const named = sel.match(/^(\w+)\[name=([^\]]+)\]$/);
    if (named) return el.tag === named[1] && el.getAttribute('name') === named[2];
    return el.tag === sel;
}

function build(options) {
    const form = makeEl('form', {'data-live-filter': ''});
    const field = makeEl('input', {'name': 'search', 'type': 'text'});
    form.appendChild(field);
    const list = makeEl('div', {'data-live-list': ''});
    const timers = [];
    const sandbox = {
        document: {
            querySelectorAll: (sel) => (sel === 'form[data-live-filter]' && options.live ? [form] : []),
            querySelector: (sel) => (sel === '[data-live-list]' ? list : null),
        },
        setTimeout: (fn, delay) => {
            timers.push({fn, delay, cancelled: false});
            return timers.length - 1;
        },
        clearTimeout: (handle) => { timers[handle].cancelled = true; },
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);
    return {
        form, field, list, timers,
        type: (value) => {
            field.value = value;
            for (const fn of (field._handlers.input || [])) fn({type: 'input', target: field});
        },
        flush: () => {
            for (const timer of timers) {
                if (!timer.cancelled) timer.fn();
            }
            return timers.length;
        },
    };
}

const live = build({live: true});

assert.strictEqual(live.form.submitted, 0, 'страница отправила форму сама при открытии');
assert.strictEqual(live.list.getAttribute('aria-busy'), null);

// Одно нажатие — одна отправка, и не раньше паузы.
live.type('Г');
assert.strictEqual(live.form.submitted, 0, 'форма ушла на первом же символе');
assert.strictEqual(live.list.getAttribute('aria-busy'), 'true',
    'пока ждём, список должен быть помечен занятым');
assert.strictEqual(live.timers[0].delay, 300);
live.flush();
assert.strictEqual(live.form.submitted, 1);

// Слово из шести букв — всё равно одна отправка, и в форме последняя буква.
const word = build({live: true});
for (const letter of 'Гвоздь'.split('')) word.type(letter);
assert.strictEqual(word.form.submitted, 0);
assert.strictEqual(word.timers.length, 6);
assert.strictEqual(word.timers.filter(t => t.cancelled).length, 5,
    'прежние паузы не отменены, значит уйдёт шесть запросов');
word.flush();
assert.strictEqual(word.form.submitted, 1);
assert.strictEqual(word.field.value, 'ь');

// Очистка тоже фильтрует: список возвращается целиком.
word.type('');
word.flush();
assert.strictEqual(word.form.submitted, 2);
assert.strictEqual(word.field.value, '');

// Форма без пометки не трогается: пауза была бы лишней на других страницах.
const off = build({live: false});
assert.strictEqual(off.form.submitted, 0);
assert.strictEqual(off.timers.length, 0);

console.log('ok');
