const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.strictEqual(blocks.length, 1,
    'ожидался ровно один блок <script>, получено ' + blocks.length);
const block = blocks[0];

function matches(el, sel) {
    const cls = sel.match(/^\.([\w-]+)$/);
    if (cls) {
        return String(el.className).split(/\s+/).indexOf(cls[1]) !== -1;
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

function makeEl(tag, attrs) {
    const el = {
        tag,
        attrs: Object.assign({}, attrs || {}),
        dataset: {},
        children: [],
        parentNode: null,
        value: '',
        textContent: '',
        innerHTML: '',
        className: (attrs && attrs.class) || '',
        _handlers: {},
    };
    for (const key of Object.keys(el.attrs)) {
        if (key === 'class') continue;
        if (key.indexOf('data-') === 0) el.dataset[key.slice(5)] = String(el.attrs[key]);
    }
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

function makePicker(kind, value, label) {
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
    box.appendChild(makeEl('ul', {'class': 'app-picker-list'}));
    box.appendChild(makeEl('p', {'class': 'app-picker-note'}));
    return {
        box, hidden, text,
        list: box.querySelector('.app-picker-list'),
        note: box.querySelector('.app-picker-note'),
    };
}

function run(payloads, options) {
    options = options || {};
    const pickers = options.pickers || [makePicker('customers', '7', 'ООО Строй')];
    const form = makeEl('form', {'data-picker-form': ''});
    const outside = makeEl('div');
    for (const picker of pickers) {
        form.appendChild(picker.box);
    }
    const requests = [];
    const timers = [];
    const documentHandlers = {};
    const documentStub = {
        querySelectorAll: (sel) => {
            if (sel === '.app-picker') return pickers.map(p => p.box);
            if (sel === '.app-picker[data-open="true"]') {
                return pickers.map(p => p.box)
                    .filter(b => b.getAttribute('data-open') === 'true');
            }
            assert.strictEqual(sel, '.app-picker');
            return pickers.map(p => p.box);
        },
        querySelector: (sel) => {
            if (sel === 'form[data-picker-form]') return form;
            if (sel === '.app-picker') return pickers[0].box;
            return outside;
        },
        createElement: (tag) => makeEl(tag, {}),
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
        fetch: (url) => {
            requests.push(url);
            return Promise.resolve({
                json: () => Promise.resolve(payloads[requests.length - 1] || payloads[0]),
            });
        },
        WeakMap,
        encodeURIComponent,
    };
    vm.createContext(sandbox);
    vm.runInContext(block, sandbox);

    const fire = (target, type, extra) => dispatch(target, type, extra, documentHandlers);
    return {
        pickers, form, requests, timers, outside,
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

    // 4. Слишком короткий запрос в базу не ходит.
    const short = run([THREE]);
    short.type(short.pickers[0], 'Г');
    await short.flush();
    assert.deepStrictEqual(short.requests, []);

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

    console.log('ok');
})().catch((e) => {
    console.error(e.stack || e.message);
    process.exit(1);
});
