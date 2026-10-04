const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.strictEqual(blocks.length, 1,
    'ожидался ровно один блок <script>, получено ' + blocks.length);
let block = blocks[0];

/* Прибор гоняет исходник шаблона, а не отрендеренную страницу, поэтому
   подстановки Jinja в нём остаются. Значения приходят из приложения (третий
   аргумент) — иначе прибор проверял бы свою копию числа, а расхождение
   заметил бы только владелец, молча, отказом на первой букве. Неизвестная
   подстановка — ошибка прибора, а не «undefined»: молчаливый undefined прогонял
   бы сценарий на несуществующем коде. */
const VALUES = JSON.parse(process.argv[3] || '{}');
const unknown = [];
block = block.replace(/\{\{\s*([A-Za-z_]\w*)\s*\}\}/g, (whole, name) => {
    if (!(name in VALUES)) { unknown.push(name); return 'undefined'; }
    /* Значения — числа и голые идентификаторы, поэтому подставляем их как есть.
   Строка в кавычках пришла бы в скрипт вместе с кавычками. */
    return String(VALUES[name]);
});
assert.deepStrictEqual(unknown, [],
    'в скрипте подстановки, которых нет в приборе: ' + unknown.join(', '));

function matches(el, sel) {
    const cls = sel.match(/^\.([\w-]+)$/);
    if (cls) {
        return String(el.className).split(/\s+/).indexOf(cls[1]) !== -1;
    }
    /* Селектор по признаку: `[data-modal-close]` и `[data-picker-browse]`. Им
       пользуется сам скрипт, в том числе на кнопках, добавленных строкой
       позже, поэтому узнать их без этой ветки нельзя. */
    const only = sel.match(/^\[([\w-]+)(?:=([^\]]+))?\]$/);
    if (only) {
        return only[2] === undefined
            ? el.getAttribute(only[1]) !== null
            : el.getAttribute(only[1]) === only[2];
    }
    const attr = sel.match(/^(\w+)\[([\w-]+)=([^\]]+)\]$/);
    if (attr) {
        return el.tag === attr[1] && el.getAttribute(attr[2]) === attr[3];
    }
    return el.tag === sel;
}

function findIn(root, sel) {
    for (const child of root.children) {
        if (matches(child, sel)) return child;
        const deep = findIn(child, sel);
        if (deep) return deep;
    }
    return null;
}

function findAll(root, sel) {
    const out = [];
    (function walk(node) {
        for (const child of node.children) {
            if (matches(child, sel)) out.push(child);
            walk(child);
        }
    })(root);
    return out;
}

function makeEl(tag, attrs) {
    const el = {
        tag,
        attrs: Object.assign({}, attrs || {}),
        dataset: {},
        children: [],
        parentNode: null,
        value: '',
        textContent: '',
        className: (attrs && attrs.class) || '',
        _handlers: {},
    };
    for (const key of Object.keys(el.attrs)) {
        if (key === 'class') continue;
        if (key.indexOf('data-') === 0) el.dataset[key.slice(5)] = String(el.attrs[key]);
    }
    /* `innerHTML = ""` у настоящего элемента вычищает детей, и скрипт на этом
       держится: без очистки новый список дописывался бы к старому, и человек
       выбрал бы товар из прошлого запроса. */
    let html = '';
    Object.defineProperty(el, 'innerHTML', {
        get: () => html,
        set: (raw) => {
            html = String(raw);
            if (html === '') el.children.length = 0;
        },
    });
    el.setAttribute = (key, raw) => {
        el.attrs[key] = String(raw);
        if (key.indexOf('data-') === 0) el.dataset[key.slice(5)] = String(raw);
    };
    el.getAttribute = (key) => (key in el.attrs ? el.attrs[key] : null);
    el.addEventListener = (type, fn) => {
        (el._handlers[type] = el._handlers[type] || []).push(fn);
    };
    el.appendChild = (child) => {
        child.parentNode = el;
        el.children.push(child);
        return child;
    };
    el.querySelector = (sel) => findIn(el, sel);
    el.closest = (sel) => {
        let node = el;
        while (node) {
            if (matches(node, sel)) return node;
            node = node.parentNode;
        }
        return null;
    };
    el.contains = (other) => {
        let node = other;
        while (node) {
            if (node === el) return true;
            node = node.parentNode;
        }
        return false;
    };
    el.focus = () => { el.focused = true; };
    el.select = () => { el.selected = true; };
    /* Список подстановки `position: fixed`, и скрипт ставит ему координаты из
       поля. У выдуманных элементов этих трёх вещей не было, и скрипт падал на
       первом же открытом списке. */
    el.style = {};
    el.offsetHeight = 96;
    el.getBoundingClientRect = () => (el.rect || {
        left: 40, right: 240, top: 100, bottom: 140, width: 200, height: 40,
    });
    return el;
}

function dispatch(target, type, extra, documentHandlers) {
    const event = Object.assign({type, target, prevented: false, defaultPrevented: false}, extra || {});
    event.preventDefault = () => { event.prevented = true; event.defaultPrevented = true; };
    let node = target;
    while (node) {
        for (const fn of (node._handlers[type] || [])) fn(event);
        node = node.parentNode;
    }
    for (const fn of ((documentHandlers || {})[type] || [])) fn(event);
    return event;
}

function makePicker(kind, value, label, blocked) {
    const box = makeEl('div', {
        'class': 'app-picker',
        'data-lookup': '/api/lookup/' + kind,
        'data-open': 'false',
        'data-value': value,
        'data-label': label,
    });
    const hidden = makeEl('input', {type: 'hidden', name: 'x'});
    hidden.value = value;
    const text = makeEl('input', {type: 'text'});
    text.value = label;
    box.appendChild(hidden);
    box.appendChild(text);
    if (kind === 'products') {
        box.appendChild(makeEl('button', {'class': 'app-btn', 'data-picker-browse': ''}));
        box.appendChild(makeEl('button', {
            'class': 'app-btn', 'data-picker-create': '',
            ...(blocked ? {'disabled': 'disabled'} : {}),
        }));
    }
    box.appendChild(makeEl('ul', {'class': 'app-picker-list'}));
    box.appendChild(makeEl('p', {'class': 'app-picker-note'}));
    const picker = {
        box, hidden, text,
        browse: findIn(box, '[data-picker-browse]'),
        create: findIn(box, '[data-picker-create]'),
        list: box.querySelector('.app-picker-list'),
        note: box.querySelector('.app-picker-note'),
    };
    if (picker.browse) picker.browse.textContent = 'Выбрать товар';
    if (picker.create) picker.create.textContent = 'Создать товар';
    return picker;
}

function makeItemsBody() {
    const body = makeEl('tbody', {id: 'items-body'});
    body.appendChild(makeRow());
    return body;
}

function makeRow() {
    const tr = makeEl('tr');
    const cell = makeEl('td');
    cell.appendChild(makePicker('products', '', '').box);
    tr.appendChild(cell);
    return tr;
}

/* Настоящая страница добавляет строку из `<template id="item-row">`, и скрипт
   берёт `tpl.content.firstElementChild.cloneNode(true)`. У выдуманных элементов
   этого нет, и проверка «строка добавилась» прошла бы в пустоту: скрипт упал бы
   на первом же `document.getElementById`. */
function makeRowTemplate() {
    const tpl = makeEl('template', {id: 'item-row'});
    const sample = makeRow();
    sample.cloneNode = () => makeRow();
    tpl.content = {firstElementChild: sample};
    return tpl;
}

function makeBrowseModal() {
    const modal = makeEl('div', {
        'class': 'app-modal', 'data-open': 'false', 'data-modal': 'browse',
    });
    const panel = makeEl('div', {'class': 'app-modal-panel'});
    const close = makeEl('button', {'class': 'app-btn', 'data-modal-close': ''});
    close.textContent = 'Закрыть';
    const input = makeEl('input', {type: 'search', 'data-modal-query': ''});
    const note = makeEl('p', {'class': 'app-picker-note', 'data-modal-note': ''});
    const list = makeEl('ul', {'class': 'app-modal-list', 'data-modal-list': ''});
    panel.appendChild(close);
    panel.appendChild(input);
    panel.appendChild(note);
    panel.appendChild(list);
    modal.appendChild(panel);
    return {modal, close, input, note, list};
}

function makeCreateModal() {
    const modal = makeEl('div', {
        'class': 'app-modal', 'data-open': 'false', 'data-modal': 'create',
    });
    const panel = makeEl('div', {'class': 'app-modal-panel'});
    const close = makeEl('button', {'class': 'app-btn', 'data-modal-close': ''});
    close.textContent = 'Закрыть';
    const form = makeEl('form', {'class': 'space-y-2', 'data-create-form': ''});
    const name = makeEl('input', {name: 'name'});
    const price = makeEl('input', {name: 'price'});
    price.value = '12.50';
    const note = makeEl('p', {'class': 'app-picker-note'});
    const submit = makeEl('button', {'class': 'app-btn-accent'});
    submit.textContent = 'Создать';
    form.appendChild(name);
    form.appendChild(price);
    form.appendChild(note);
    form.appendChild(submit);
    panel.appendChild(close);
    panel.appendChild(form);
    modal.appendChild(panel);
    return {modal, close, form, name, price, note, submit};
}

function run(payloads, options) {
    options = options || {};
    const pickers = options.pickers || [makePicker('customers', '7', 'ООО Строй')];
    const form = makeEl('form', {'data-picker-form': ''});
    const outside = makeEl('div');
    for (const picker of pickers) {
        form.appendChild(picker.box);
    }
    const browse = makeBrowseModal();
    const create = makeCreateModal();
    const addButton = makeEl('button', {'class': 'app-btn', 'data-item-add': ''});
    addButton.textContent = 'Добавить строку';
    const items = makeItemsBody();
    const template = makeRowTemplate();
    const root = makeEl('body');
    form.appendChild(addButton);
    form.appendChild(items);
    root.appendChild(form);
    root.appendChild(template);
    root.appendChild(browse.modal);
    root.appendChild(create.modal);
    root.appendChild(outside);
    const requests = [];
    const calls = [];
    const timers = [];
    const documentHandlers = {};
    const documentStub = {
        querySelectorAll: (sel) => {
            if (sel === '.app-picker') return pickers.map(p => p.box);
            if (sel === '.app-picker[data-open="true"]') {
                return pickers.map(p => p.box)
                    .filter(b => b.getAttribute('data-open') === 'true');
            }
            assert.ok(sel === '[data-modal-close]',
                'неожиданный querySelectorAll: ' + sel);
            return findAll(root, sel);
        },
        querySelector: (sel) => {
            if (sel === 'form[data-picker-form]') return form;
            if (sel === '.app-picker') return pickers[0].box;
            return findIn(root, sel);
        },
        createElement: (tag) => makeEl(tag, {}),
        getElementById: (id) => (id === 'items-body' ? items
            : id === 'item-row' ? template : null),
        addEventListener: (type, fn) => {
            (documentHandlers[type] = documentHandlers[type] || []).push(fn);
        },
        documentElement: {clientHeight: 900},
    };
    const windowHandlers = {};
    const sandbox = {
        document: documentStub,
        window: {
            addEventListener: (type, fn) => {
                (windowHandlers[type] = windowHandlers[type] || []).push(fn);
            },
        },
        setTimeout: (fn, delay) => {
            timers.push({fn, delay, cancelled: false});
            return timers.length - 1;
        },
        clearTimeout: (handle) => { timers[handle].cancelled = true; },
        fetch: (url, sent) => {
            requests.push(url);
            calls.push({url, sent});
            const index = requests.length - 1;
            const payload = payloads[index] !== undefined ? payloads[index] : payloads[0];
            return Promise.resolve({
                ok: !(payload && payload.ok === false),
                json: () => Promise.resolve(payload),
            });
        },
        WeakMap,
        encodeURIComponent,
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);

    const fire = (target, type, extra) => dispatch(target, type, extra, documentHandlers);
    return {
        pickers, form, requests, calls, timers, outside, browse, create,
        addButton, items, template, fire,
        flush: () => {
            for (const timer of timers) {
                if (!timer.cancelled) timer.fn();
            }
            return new Promise(resolve => setTimeout(resolve, 0));
        },
        type: (picker, value) => {
            picker.text.value = value;
            return fire(picker.text, 'input');
        },
        key: (picker, key) => fire(picker.text, 'keydown', {key}),
        focus: (picker) => fire(picker.text, 'focusin'),
        submit: () => fire(form, 'submit'),
        clickList: (picker, index) => fire(picker.list.children[index], 'click'),
        clickOutside: () => fire(outside, 'click'),
        click: (node) => fire(node, 'click'),
        press: (node, key) => fire(node, 'keydown', {key}),
        submitForm: (node) => fire(node, 'submit'),
        typeModal: (value) => {
            browse.input.value = value;
            return fire(browse.input, 'input');
        },
        scroll: (dy) => {
            for (const picker of pickers) {
                picker.text.rect = Object.assign({}, picker.text.rect, {
                    top: 100 + dy, bottom: 140 + dy,
                });
            }
            for (const fn of (documentHandlers.scroll || [])) {
                fn({type: 'scroll'});
            }
        },
        resize: () => {
            for (const fn of (windowHandlers.resize || [])) fn({type: 'resize'});
        },
    };
}

const THREE = {
    items: [
        {id: 3, label: 'Гвоздь'},
        {id: 4, label: 'Гвоздь усиленный'},
        {id: 5, label: 'Гвоздь 3мм'},
    ],
    total: 3,
    limit: 10,
};

(async () => {
    // 1. При открытии страницы ничего не теряется и ничего не запрашивается.
    const start = run([THREE]);
    assert.deepStrictEqual(start.requests, [], 'страница не должна ничего запрашивать сама');
    assert.strictEqual(start.pickers[0].hidden.value, '7');
    assert.strictEqual(start.pickers[0].text.value, 'ООО Строй');
    assert.strictEqual(start.pickers[0].box.getAttribute('data-open'), 'false');

    // 2. Фокус выделяет текст, чтобы набор шёл поверх сохранённого имени.
    start.focus(start.pickers[0]);
    assert.ok(start.pickers[0].text.selected, 'фокус не выделил сохранённое имя');

    // 3. Три нажатия — один запрос: пауза живёт в браузере.
    const typed = run([THREE]);
    typed.type(typed.pickers[0], 'Гво');
    typed.type(typed.pickers[0], 'Гвоз');
    typed.type(typed.pickers[0], 'Гвозд');
    assert.deepStrictEqual(typed.requests, [], 'запрос ушел раньше паузы');
    assert.strictEqual(typed.timers.length, 3);
    assert.strictEqual(typed.timers[0].delay, 300);
    assert.ok(typed.timers[0].cancelled && typed.timers[1].cancelled,
        'прежние паузы не отменены, значит уйдет несколько запросов');
    await typed.flush();
    assert.deepStrictEqual(typed.requests,
        ['/api/lookup/customers?q=' + encodeURIComponent('Гвозд')]);
    assert.strictEqual(typed.pickers[0].list.children.length, 3);
    assert.strictEqual(typed.pickers[0].box.getAttribute('data-open'), 'true');
    assert.strictEqual(typed.pickers[0].text.getAttribute('aria-expanded'), 'true');
    for (const option of typed.pickers[0].list.children) {
        assert.strictEqual(option.getAttribute('role'), 'option');
        assert.ok(String(option.textContent).length, 'пункт без подписи');
    }

    // 4. Одна буква — уже намерение искать: уходит запрос с той же паузой.
    //     Порог подстановки — один символ, и он отличается от порога глобального
    //     поиска намеренно (причина и измерения — в `_lookup_query`).
    const short = run([THREE]);
    short.type(short.pickers[0], 'Г');
    assert.deepStrictEqual(short.requests, [], 'запрос ушел раньше паузы');
    await short.flush();
    assert.deepStrictEqual(short.requests,
        ['/api/lookup/customers?q=' + encodeURIComponent('Г')],
        'одна буква не дошла до сервера: ' + short.requests);
    assert.strictEqual(short.pickers[0].list.children.length, 3,
        'ответ на одну букву не показан');

    // 5. Стрелки ведут по списку, Enter выбирает.
    const keys = run([THREE]);
    keys.type(keys.pickers[0], 'Гвозд');
    await keys.flush();
    keys.key(keys.pickers[0], 'ArrowDown');
    assert.strictEqual(keys.pickers[0].list.children[0].getAttribute('aria-selected'), 'true');
    keys.key(keys.pickers[0], 'ArrowDown');
    assert.strictEqual(keys.pickers[0].list.children[0].getAttribute('aria-selected'), 'false');
    assert.strictEqual(keys.pickers[0].list.children[1].getAttribute('aria-selected'), 'true');
    const enter = keys.key(keys.pickers[0], 'Enter');
    assert.ok(enter.prevented, 'Enter не потушен, форма уйдет с чужим текстом');
    assert.strictEqual(keys.pickers[0].hidden.value, '4');
    assert.strictEqual(keys.pickers[0].text.value, 'Гвоздь усиленный');
    assert.strictEqual(keys.pickers[0].box.getAttribute('data-open'), 'false');
    keys.key(keys.pickers[0], 'ArrowUp');
    assert.strictEqual(keys.pickers[0].box.getAttribute('data-open'), 'false',
        'список закрыт, стрелка не должна его открывать');

    // 6. Escape закрывает список, Enter без отмеченного пункта — нет.
    const esc = run([THREE]);
    esc.type(esc.pickers[0], 'Гвозд');
    await esc.flush();
    assert.strictEqual(esc.pickers[0].box.getAttribute('data-open'), 'true');
    esc.key(esc.pickers[0], 'Escape');
    assert.strictEqual(esc.pickers[0].box.getAttribute('data-open'), 'false');
    assert.ok(!esc.key(esc.pickers[0], 'Enter').prevented,
        'Enter без выбора не должен мешать форме');

    // 7. Клик мышью выбирает.
    const clicked = run([THREE]);
    clicked.type(clicked.pickers[0], 'Гвозд');
    await clicked.flush();
    clicked.clickList(clicked.pickers[0], 2);
    assert.strictEqual(clicked.pickers[0].hidden.value, '5');
    assert.strictEqual(clicked.pickers[0].text.value, 'Гвоздь 3мм');

    // 8. Свободный текст ни к чему не привязан и форму не выпускает.
    const loose = run([{items: [], total: 0, limit: 10}]);
    loose.type(loose.pickers[0], 'несуществующий');
    await loose.flush();
    assert.strictEqual(loose.pickers[0].hidden.value, '',
        'идентификатор сохранился при чужом тексте — это привяжет неверного клиента');
    assert.ok(loose.pickers[0].note.textContent.indexOf('Ничего не найдено') !== -1,
        loose.pickers[0].note.textContent);
    assert.ok(loose.submit().prevented, 'форма ушла с невыбранным значением');
    assert.ok(loose.pickers[0].note.textContent.indexOf('Выберите') !== -1,
        loose.pickers[0].note.textContent);
    assert.ok(loose.pickers[0].text.focused, 'фокус не вернулся в поле');

    // 9. Очистка возвращает сохранённое значение, и форму выпускает.
    loose.type(loose.pickers[0], '');
    assert.strictEqual(loose.pickers[0].hidden.value, '7');
    assert.strictEqual(loose.pickers[0].text.value, 'ООО Строй');
    assert.ok(!loose.submit().prevented);

    // 10. Обрезка списка видна словами, а не молчанием.
    const capped = run([{items: THREE.items, total: 214, limit: 10}]);
    capped.type(capped.pickers[0], 'Гвозд');
    await capped.flush();
    assert.ok(capped.pickers[0].note.textContent.indexOf('первые 10 из 214') !== -1,
        capped.pickers[0].note.textContent);

    // 11. Выбранное значение — тоже сохранённое: очистка после выбора вернёт
    //     именно его, а не то, что было при открытии страницы.
    const chosen = run([THREE]);
    chosen.type(chosen.pickers[0], 'Гвозд');
    await chosen.flush();
    chosen.clickList(chosen.pickers[0], 0);
    chosen.type(chosen.pickers[0], ' ');
    chosen.type(chosen.pickers[0], '');
    assert.strictEqual(chosen.pickers[0].hidden.value, '3');
    assert.strictEqual(chosen.pickers[0].text.value, 'Гвоздь');

    // 12. Строка, добавленная кнопкой, тоже работает: обработчики висят на
    //     форме, а не на поле, поэтому клон подхватывается сам.
    const added = run([THREE]);
    const late = makePicker('products', '', '');
    added.form.appendChild(late.box);
    added.type(late, 'Гвозд');
    await added.flush();
    assert.strictEqual(added.requests.length, 1);
    assert.strictEqual(late.list.children.length, 3);

    // 13. Клик мимо поля закрывает список.
    const away = run([THREE]);
    away.type(away.pickers[0], 'Гвозд');
    await away.flush();
    away.clickOutside();
    assert.strictEqual(away.pickers[0].box.getAttribute('data-open'), 'false');

    // 14. Открытый список встаёт под своё поле: без координат список `fixed`
    //     повис бы в углу, а при прокрутке оторвался бы от поля.
    const placed = run([THREE]);
    placed.type(placed.pickers[0], 'Гвозд');
    await placed.flush();
    assert.deepStrictEqual(
        {
            top: placed.pickers[0].list.style.top,
            left: placed.pickers[0].list.style.left,
            width: placed.pickers[0].list.style.width,
        },
        {top: '142px', left: '40px', width: '200px'},
        'список не встал под поле');
    placed.scroll(150);
    assert.strictEqual(placed.pickers[0].list.style.top, '292px',
        'прокрутка не вернула список под поле');
    placed.resize();
    assert.strictEqual(placed.pickers[0].list.style.top, '292px');

// 15. Пустое поле нового заказа при фокусе просит последние выбранные.
    const RECENT = {
        items: [{id: 11, label: 'Гвоздь'}, {id: 12, label: 'Шуруп'}],
        total: 2, limit: 10, recent: true,
    };
    const fresh = run([RECENT], {pickers: [makePicker('customers', '', '')]});
    fresh.focus(fresh.pickers[0]);
    await fresh.flush();
    assert.strictEqual(fresh.requests.length, 1, 'пустое поле не спросило последние выбранные');
    assert.strictEqual(fresh.requests[0], '/api/lookup/customers?q=',
        'запрос пошёл не на подстановку: ' + fresh.requests[0]);
    assert.strictEqual(fresh.pickers[0].list.children.length, 2);
    assert.strictEqual(fresh.pickers[0].note.textContent, 'Последние выбранные',
        'список последних не отличить от результатов поиска');

    // 16. Заполненное поле молчит: там уже есть значение, и список только мешал бы.
    const filled = run([RECENT]);
    filled.focus(filled.pickers[0]);
    await filled.flush();
    assert.strictEqual(filled.requests.length, 0,
        'поле с выбранным значением запросило последние выбранные');
    assert.strictEqual(filled.pickers[0].box.getAttribute('data-open'), 'false');

    // 17. Начатый набор отменяет последние: человек ищет, а не выбирает из того,
    //     что и так на руках.
    const typing = run([RECENT, THREE], {pickers: [makePicker('customers', '', '')]});
    typing.focus(typing.pickers[0]);
    await typing.flush();
    typing.type(typing.pickers[0], 'Гвозд');
    await typing.flush();
    assert.strictEqual(typing.requests.length, 2);
    assert.ok(/q=%D0%93%D0%B2%D0%BE%D0%B7%D0%B4/.test(typing.requests[1]),
        'второй запрос не про набор: ' + typing.requests[1]);
    assert.strictEqual(typing.pickers[0].note.textContent, '',
        'подсказка про последние выбранные осталась после набора');

    // 18. «Выбрать товар» открывает окно и спрашивает ТОТ ЖЕ адрес подстановки.
    //     Второй путь запросов разошёлся бы с полем, и заметил бы это только тот,
    //     кто искал одно и то же дважды.
    const RECENT_PRODUCTS = {
        items: [{id: 21, label: 'Гвоздь', hint: '10.00 ₽ · ост. 5'},
                {id: 22, label: 'Шуруп', hint: '12.00 ₽ · ост. 7'}],
        total: 2, limit: 10, recent: true,
    };
    const browse = run([RECENT_PRODUCTS], {pickers: [makePicker('products', '', '')]});
    browse.click(browse.pickers[0].browse);
    await browse.flush();
    assert.strictEqual(browse.browse.modal.getAttribute('data-open'), 'true',
        'окно выбора товара не открылось');
    assert.deepStrictEqual(browse.requests, ['/api/lookup/products?q='],
        'окно пошло не на подстановку: ' + browse.requests[0]);
    assert.strictEqual(browse.browse.list.children.length, 2);
    assert.ok(browse.browse.input.focused, 'фокус остался на странице, а не в окне');
    assert.strictEqual(browse.browse.note.textContent, 'Последние выбранные');
    const labels = RECENT_PRODUCTS.items.map((row) => row.label);
    browse.browse.list.children.forEach((row, index) => {
        const button = row.children[0];
        assert.strictEqual(button.tag, 'button',
            'пункт окна должен быть кнопкой, иначе с клавиатуры не выбрать');
        const states = String(button.className).split(/\s+/)
            .filter((c) => ['app-link', 'app-btn', 'app-btn-accent', 'app-btn-line',
                            'app-btn-danger', 'app-nav-link'].indexOf(c) !== -1);
        assert.strictEqual(states.length, 1, 'состояний у пункта: ' + states);
        assert.ok(String(button.textContent).indexOf(labels[index]) === 0,
            `пункт ${index}: ${button.textContent}`);
    });

    // 19. Набор в окне идёт с той же паузой и на тот же адрес.
    browse.typeModal('Шуп');
    assert.deepStrictEqual(browse.requests, ['/api/lookup/products?q='],
        'запрос ушёл раньше паузы');
    await browse.flush();
    assert.strictEqual(browse.requests.length, 2);
    assert.strictEqual(browse.requests[1],
        '/api/lookup/products?q=' + encodeURIComponent('Шуп'));
    assert.strictEqual(browse.browse.list.children.length, 2,
        'старые пункты остались в окне: выбрать можно было бы товар прошлого запроса');

    // 20. Клик по пункту заполняет строку и закрывает окно, фокус возвращается
    //     в поле строки — иначе человек продолжал бы печатать в закрытом окне.
    browse.click(browse.browse.list.children[0].children[0]);
    assert.strictEqual(browse.pickers[0].hidden.value, '21');
    assert.strictEqual(browse.pickers[0].text.value, 'Гвоздь');
    assert.strictEqual(browse.browse.modal.getAttribute('data-open'), 'false');
    assert.ok(browse.pickers[0].text.focused, 'фокус не вернулся в строку заказа');

    // 21. Escape закрывает окно, ничего не выбирая.
    const escModal = run([RECENT_PRODUCTS], {pickers: [makePicker('products', '9', 'Доска')]});
    escModal.click(escModal.pickers[0].browse);
    await escModal.flush();
    escModal.press(escModal.browse.input, 'Escape');
    assert.strictEqual(escModal.browse.modal.getAttribute('data-open'), 'false',
        'Escape не закрыл окно выбора товара');
    assert.strictEqual(escModal.pickers[0].hidden.value, '9', 'Escape подменил товар');
    assert.strictEqual(escModal.create.modal.getAttribute('data-open'), 'false');

    // 22. Созданный товар сразу становится выбранным в этой строке.
    const created = run([{id: 31, name: 'Гвоздь 3мм'}],
                        {pickers: [makePicker('products', '', '')]});
    created.click(created.pickers[0].create);
    assert.strictEqual(created.create.modal.getAttribute('data-open'), 'true',
        'форма создания не открылась');
    created.create.name.value = 'Гвоздь 3мм';
    created.submitForm(created.create.form);
    await created.flush();
    const sent = created.calls[0];
    assert.strictEqual(sent.url, '/api/products', 'создание пошло не в API товаров');
    assert.strictEqual(sent.sent.method, 'POST');
    assert.deepStrictEqual(JSON.parse(sent.sent.body),
        {name: 'Гвоздь 3мм', price: 12.5}, sent.sent.body);
    assert.strictEqual(created.pickers[0].hidden.value, '31',
        'новый товар не стал выбранным в строке заказа');
    assert.strictEqual(created.pickers[0].text.value, 'Гвоздь 3мм');
    assert.strictEqual(created.create.modal.getAttribute('data-open'), 'false');

    // 23. Отказ сервера виден словами и строку не трогает.
    const refused = run([{ok: false, detail: 'Название обязательно'}],
                        {pickers: [makePicker('products', '', '')]});
    refused.click(refused.pickers[0].create);
    refused.create.name.value = '   ';
    refused.submitForm(refused.create.form);
    await refused.flush();
    assert.strictEqual(refused.create.note.textContent, 'Название обязательно',
        'отказ сервера не показан: ' + refused.create.note.textContent);
    assert.strictEqual(refused.create.modal.getAttribute('data-open'), 'true',
        'окно закрылось, хотя товар не создан');
    assert.strictEqual(refused.pickers[0].hidden.value, '',
        'в строку попал товар, которого нет');

    // 24. Погашенная кнопка не открывает ничего: `disabled` — не только картинка,
    //     но и в разметке, и в обработчике.
    const blocked = run([{id: 41, name: 'Гвоздь'}],
                        {pickers: [makePicker('products', '', '', true)]});
    blocked.click(blocked.pickers[0].create);
    assert.strictEqual(blocked.create.modal.getAttribute('data-open'), 'false',
        'погашенная кнопка всё равно открыла форму создания');

    // 25. Строка, добавленная позже, работает: обработчики висят на форме, а не
    //     на кнопках, иначе клон из <template> остался бы с мёртвыми кнопками.
    const lateLine = run([RECENT_PRODUCTS], {pickers: [makePicker('products', '', '')]});
    const extra = makePicker('products', '', '');
    lateLine.form.appendChild(extra.box);
    lateLine.click(extra.browse);
    await lateLine.flush();
    assert.strictEqual(lateLine.browse.modal.getAttribute('data-open'), 'true');
    lateLine.click(lateLine.browse.list.children[1].children[0]);
    assert.strictEqual(extra.hidden.value, '22');
    assert.strictEqual(lateLine.pickers[0].hidden.value, '',
        'выбор ушёл не в ту строку');

    // 26. «Добавить строку» действительно добавляет строку. Раньше кнопка несла
    //     `onclick="addItemRow()"`, а функция объявлена внутри IIFE и в глобальной
    //     области не существует: настоящий Chrome даёт
    //     `Uncaught ReferenceError: addItemRow is not defined` и кнопка молчит.
    const grown = run([THREE]);
    assert.strictEqual(grown.items.children.length, 1, 'изначально одна строка');
    grown.click(grown.addButton);
    assert.strictEqual(grown.items.children.length, 2,
        'кнопка «Добавить строку» не добавила строку');
    grown.click(grown.addButton);
    assert.strictEqual(grown.items.children.length, 3, 'и второй раз тоже');

    // 27. Добавленная строка — рабочая: её подстановка спрашивает и заполняется.
    const freshRow = grown.items.children[2].children[0].children[0];
    assert.strictEqual(freshRow.getAttribute('data-lookup'), '/api/lookup/products',
        'в добавленной строке не та подстановка');
    freshRow.querySelector('input[type=text]').value = 'Гвозд';
    grown.fire(freshRow.querySelector('input[type=text]'), 'input');
    await grown.flush();
    assert.ok(/%D0%93%D0%B2%D0%BE%D0%B7%D0%B4/.test(grown.requests[0]),
        'добавленная строка не ищет: ' + grown.requests[0]);
    assert.strictEqual(freshRow.querySelector('.app-picker-list').children.length, 3);
    grown.click(freshRow.querySelector('.app-picker-list').children[0]);
    assert.strictEqual(freshRow.querySelector('input[type=hidden]').value, '3',
        'выбор в добавленной строке не записался');
    assert.strictEqual(grown.items.children[0].children[0]
        .querySelector('input[type=hidden]').value, '',
        'выбор ушёл не в ту строку');

    console.log('ok');
})().catch((e) => {
    console.error(e.stack || e.message);
    process.exit(1);
});
