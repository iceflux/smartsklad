const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...template.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const ours = blocks.filter(b => b.indexOf('liveFilterDebounce') !== -1);
assert.strictEqual(ours.length, 1,
    'ожидался ровно один блок с живым фильтром, получено ' + ours.length);
const block = ours[0];

function matches(el, sel) {
    const cls = sel.match(/^\.([\w-]+)$/);
    if (cls) return String(el.className || '').split(/\s+/).indexOf(cls[1]) !== -1;
    const attr = sel.match(/^\[([\w-]+)=([^\]]+)\]$/);
    if (attr) return el.getAttribute(attr[1]) === attr[2];
    const named = sel.match(/^(\w+)\[name=([^\]]+)\]$/);
    if (named) return el.tag === named[1] && el.getAttribute('name') === named[2];
    return el.tag === sel;
}

function makeEl(tag, attrs) {
    const el = {
        tag,
        attrs: Object.assign({}, attrs || {}),
        children: [],
        fields: [],
        value: '',
        innerHTML: 'было',
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
        for (const child of el.fields) {
            if (matches(child, sel)) return child;
        }
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

function build(options) {
    const form = makeEl('form', {'data-live-filter': ''});
    const field = makeEl('input', {'name': 'search', 'type': 'text'});
    form.fields.push(field);
    form.fields.push(makeEl('input', {'name': 'sort', 'type': 'hidden'}));
    form.action = 'https://localhost/products';
    const list = makeEl('div', {'data-live-list': ''});
    const timers = [];
    const requests = [];
    const history = [];
    const pending = [];
    /* Ответ едет вместе с запросом: `byHtml` отдаёт разбору именно тот текст,
       который пришёл с этим запросом. Если разбор брал бы «текущий», старый
       ответ принёс бы свежий текст, и проверка устаревшего ответа не могла бы
       упасть — она проходила бы по построению. */
    const byHtml = new Map();
    let respond = () => 'plain';

    const sandbox = {
        document: {
            querySelectorAll: (sel) => (sel === 'form[data-live-filter]' && options.live ? [form] : []),
            querySelector: (sel) => (sel === '[data-live-list]' ? list : null),
        },
        window: {history: {replaceState: (_a, _b, url) => history.push(url)}},
        setTimeout: (fn, delay) => {
            timers.push({fn, delay, cancelled: false});
            return timers.length - 1;
        },
        clearTimeout: (handle) => { timers[handle].cancelled = true; },
        Promise,
        URLSearchParams,
        FormData: function (target) {
            return target.fields.map(f => [f.getAttribute('name'), f.value]);
        },
        DOMParser: function () {
            return {
                parseFromString: (html) => ({
                    querySelector: (sel) => (sel === '[data-live-list]'
                        ? {innerHTML: byHtml.get(html)} : null),
                }),
            };
        },
        fetch: (url) => {
            requests.push(url);
            /* Ответ запоминается в момент запроса, а не в момент разбора: иначе
               старый ответ, пришедший вторым, принёс бы свежий текст, и проверка
               устаревшего ответа не могла бы упасть. */
            const html = respond();
            if (!options.hold) {
                return Promise.resolve({text: () => Promise.resolve(html)});
            }
            return new Promise((resolve) => pending.push({resolve, html}));
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);
    const settle = async () => {
        for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve));
    };
    return {
        form, field, list, timers, requests, history, settle,
        answer: (html) => {
            const wire = 'ОТВЕТ:' + html;
            byHtml.set(wire, html);
            respond = () => wire;
        },
        release: (index) => {
            pending[index].resolve({text: () => Promise.resolve(pending[index].html)});
        },
        type: (value) => {
            field.value = value;
            for (const fn of (field._handlers.input || [])) fn({type: 'input', target: field});
        },
        flush: async () => {
            for (const timer of timers) {
                if (!timer.cancelled) await timer.fn();
            }
            await settle();
            return timers.length;
        },
    };
}

(async () => {
    // 1. Одно нажатие — один запрос, и не раньше паузы. Форму при этом не
    //    отправляют: полная отправка перезагружает страницу и уводит фокус.
    const live = build({live: true});
    live.type('Г');
    assert.strictEqual(live.form.submitted, 0, 'страница ушла сама, фокус потеряется');
    assert.strictEqual(live.list.getAttribute('aria-busy'), 'true',
        'пока ждём, список должен быть помечен занятым');
    assert.strictEqual(live.timers[0].delay, 300);
    await live.flush();
    assert.strictEqual(live.requests.length, 1);
    assert.strictEqual(live.form.submitted, 0, 'форма отправлена — это и есть потеря фокуса');
    assert.ok(/search=%D0%93/.test(live.requests[0]),
        'в запрос не попал набранный текст: ' + live.requests[0]);

    // 2. Список и подвал с «Всего» меняются вместе, и адресная строка тоже.
    live.answer('НОВЫЙ СПИСОК');
    await live.flush();
    assert.strictEqual(live.list.innerHTML, 'НОВЫЙ СПИСОК');
    assert.strictEqual(live.list.getAttribute('aria-busy'), null,
        'список остался помечен занятым');
    assert.ok(/search=/.test(live.history[0]),
        'адресная строка не обновилась, перезагрузка потеряет фильтр: ' + live.history[0]);

    // 3. Слово из шести букв — всё равно одна отправка, и в форме последняя буква.
    const word = build({live: true});
    for (const letter of 'Гвоздь'.split('')) word.type(letter);
    assert.strictEqual(word.form.submitted, 0);
    assert.strictEqual(word.timers.length, 6);
    assert.strictEqual(word.timers.filter(t => t.cancelled).length, 5,
        'прежние паузы не отменены, значит уйдёт шесть запросов');
    await word.flush();
    assert.strictEqual(word.requests.length, 1);
    assert.strictEqual(word.field.value, 'ь');

    // 4. Очистка тоже фильтрует: список возвращается целиком.
    word.type('');
    await word.flush();
    assert.strictEqual(word.requests.length, 2);
    assert.strictEqual(word.field.value, '');

    // 5. Устаревший ответ не затирает свежий: два запроса в воздухе, и первый
    //    возвращается вторым — именно тот порядок, на котором список мигает.
    const race = build({live: true, hold: true});
    race.answer('ОТВЕТ-ГВО');
    race.type('Гво');
    await race.flush();
    race.answer('ОТВЕТ-ГВОЗДЬ');
    race.type('Гвоздь');
    await race.flush();
    assert.strictEqual(race.requests.length, 2);
    race.release(1);
    await race.settle();
    assert.strictEqual(race.list.innerHTML, 'ОТВЕТ-ГВОЗДЬ',
        'свежий ответ не применился');
    race.release(0);
    await race.settle();
    assert.strictEqual(race.list.innerHTML, 'ОТВЕТ-ГВОЗДЬ',
        'старый ответ поверх нового: список показал не то, что в поле');

    // 6. Форма без пометки не трогается: пауза была бы лишней на других страницах.
    const off = build({live: false});
    assert.strictEqual(off.form.submitted, 0);
    assert.strictEqual(off.timers.length, 0);

    console.log('ok');
})().catch((e) => {
    console.error(e.stack || e.message);
    process.exit(1);
});