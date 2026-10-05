const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

process.on('uncaughtException', (e) => { console.error(e.stack || e.message); process.exit(1); });
process.on('unhandledRejection', (e) => { console.error((e && e.stack) || e); process.exit(1); });

const template = fs.readFileSync(process.argv[2], 'utf8');
const SHIPPED_SLOW_MS = process.argv[3];
assert.ok(/^\d+$/.test(SHIPPED_SLOW_MS || ''),
    'харнесс не получил порог из настройки: ' + SHIPPED_SLOW_MS);
const blocks = [...template.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const ours = blocks.filter(b => b.indexOf('data-search-open') !== -1);
assert.strictEqual(ours.length, 1,
    'ожидался ровно один скрипт окна поиска, получено ' + ours.length);
assert.ok(!/<details/.test(template), 'раскрывающийся поиск снова в шапке');
assert.ok(!/onclick\s*=/.test(template), 'в разметке есть инлайновый обработчик');
const block = ours[0];

function abortError() {
    const error = new Error('aborted');
    error.name = 'AbortError';
    return error;
}

function el(attrs) {
    return {
        tag: 'button',
        attrs: Object.assign({}, attrs || {}),
        _handlers: {},
        _children: {},
        value: '',
        hidden: false,
        innerHTML: 'было',
        focused: 0,
        selection: null,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        addEventListener(type, fn) { (this._handlers[type] = this._handlers[type] || []).push(fn); },
        querySelector(sel) { return this._children[sel] || null; },
        focus() { this.focused += 1; PAGE.doc.activeElement = this; },
        setSelectionRange(a, b) { this.selection = [a, b]; },
        click(target) { (this._handlers.click || []).forEach(fn => fn({target: target === undefined ? this : target})); },
        key(name, event) { (this._handlers[name] || []).forEach(fn => fn(event)); },
    };
}

let PAGE = null;

function link() { return {tag: 'a', closest: (sel) => (sel === 'a' ? {} : null)}; }
function span() { return {tag: 'span', closest: () => null}; }

function build(options) {
    options = options || {};
    const opener = el({'data-search-open': '', 'aria-expanded': 'false'});
    const field = el({'data-search-field': '', 'name': 'q'});
    const results = el({'data-search-results': '', 'aria-live': 'polite'});
    const submit = el({'data-search-submit': ''});
    /* В разметке кнопка «Найти» приезжает скрытой, иначе ручной режим был бы
       включён всегда. Настоящее значение, а не заглушка: при hidden = false
       проверка «медленный поиск показал кнопку» проходила бы по построению. */
    submit.hidden = true;
    const closeBtn = el({'data-search-close': ''});
    const panel = el({class: 'app-modal-panel'});
    const modal = el({'data-modal': 'search', 'data-open': 'false', 'data-table': '/api/search/table'});
    Object.assign(modal._children, {
        '[data-search-field]': field,
        '[data-search-results]': results,
        '[data-search-submit]': submit,
        '[data-search-close]': closeBtn,
        '.app-modal-panel': panel,
    });
    const doc = {
        activeElement: opener,
        _handlers: {},
        addEventListener(type, fn) { (this._handlers[type] = this._handlers[type] || []).push(fn); },
        querySelector(sel) {
            if (sel === '[data-search-open]') return opener;
            if (sel === '[data-modal="search"]') return modal;
            return null;
        },
    };
    const timers = [];
    const requests = [];
    const byHtml = new Map();
    PAGE = {doc};

    const sandbox = {
        document: doc,
        AbortController,
        DOMParser: function () {
            return {
                parseFromString: (html) => ({
                    querySelector: (sel) => (sel === '[data-search-results]'
                        ? {innerHTML: byHtml.get(html)} : null),
                }),
            };
        },
        performance,
        encodeURIComponent,
        Promise,
        setTimeout: (fn, delay) => {
            timers.push({fn, delay, cancelled: false});
            return timers.length - 1;
        },
        clearTimeout: (handle) => { timers[handle].cancelled = true; },
        fetch: (url, init) => {
            /* Ответ запоминается вместе с запросом: разбор берёт именно тот текст,
               который пришёл с этим запросом. Иначе старый ответ принёс бы свежий,
               и проверка устаревшего ответа проходила бы по построению. */
            const html = 'ОТВЕТ ' + url;
            byHtml.set(html, options.answer || html);
            const record = {url, html, aborted: false};
            requests.push(record);
            const promise = new Promise((resolve, reject) => {
                record.settled = false;
                record.deliver = () => {
                    if (record.settled) return;
                    record.settled = true;
                    resolve({text: () => Promise.resolve(byHtml.get(html))});
                };
                record.refuse = (error) => {
                    if (record.settled) return;
                    record.settled = true;
                    reject(error);
                };
                if (init && init.signal) {
                    init.signal.addEventListener('abort', () => {
                        record.aborted = true;
                        record.refuse(abortError());
                    });
                }
                if (!options.hold) {
                    setTimeout(() => {
                        if (options.fail) record.refuse(new Error(options.fail));
                        else record.deliver();
                    }, options.slowMs || 0);
                }
            });
            return promise;
        },
    };
    /* Порог приходит в скрипт Jinja-подстановкой, а харнесс читает исходник
       шаблона. Подставляем его сами: сценарии должны уметь и включить ручной
       режим, и НЕ включить его при том же ответе — иначе «настройка доехала»
       нечем проверить. Само значение приходит аргументом из Python, чтобы
       число не расходилось с DEFAULT_SEARCH_SLOW_MS. */
    const threshold = options.threshold === undefined ? SHIPPED_SLOW_MS : String(options.threshold);
    const source = block.replace(/\{\{\s*search_slow_ms\s*\}\}/g, threshold);
    assert.notStrictEqual(source, block,
        'в скрипте окна нет подстановки порога — значит он снова зашит в шаблон');
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);

    const settle = async () => {
        /* Ожидание и через setImmediate, и через настоящий таймер: ответ приходит
           по таймеру, а цепочка промисов живёт в setImmediate, и только один из
           двух витков означал бы «дождаться нечего». */
        for (let i = 0; i < 12; i++) {
            await new Promise(resolve => setImmediate(resolve));
            await new Promise(resolve => setTimeout(resolve, 1));
        }
    };
    return {
        opener, modal, field, results, submit, closeBtn, panel, doc, requests, timers, settle,
        type: (value) => {
            field.value = value;
            field.key('input', {type: 'input', target: field});
        },
        press: (key) => {
            const event = {key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
            (doc._handlers.keydown || []).forEach(fn => fn(event));
            return event;
        },
        flush: async () => {
            /* Пауза отрабатывает один раз: иначе следующий `flush` выполнил бы её
               повторно и послал бы лишний запрос, а проверки порядка ответов
               считают запросы. */
            for (const timer of timers) {
                if (timer.cancelled || timer.ran) continue;
                timer.ran = true;
                await timer.fn();
            }
            await settle();
        },
        wait: (ms) => new Promise(resolve => setTimeout(resolve, ms)),
    };
}

(async () => {
    // 1. Одно нажатие — один запрос, и не раньше паузы. Пауза та же, что у живой
    //    фильтрации разделов: одно число на всё приложение.
    const live = build({});
    live.type('Г');
    live.type('Гв');
    live.type('Гво');
    assert.strictEqual(live.timers.length, 3);
    assert.strictEqual(live.timers.filter(t => t.cancelled).length, 2,
        'прежние паузы не отменены, значит уйдёт три запроса');
    assert.strictEqual(live.timers[0].delay, 300);
    assert.strictEqual(live.requests.length, 0, 'запрос ушёл до паузы');
    await live.flush();
    assert.strictEqual(live.requests.length, 1);
    assert.ok(/q=%D0%93%D0%B2%D0%BE/.test(live.requests[0].url),
        'в запрос не попал набранный текст: ' + live.requests[0].url);
    assert.strictEqual(live.results.innerHTML, live.requests[0].html,
        'в список попал не тот ответ, на который был запрос');
    assert.strictEqual(live.results.getAttribute('aria-busy'), null,
        'список остался помечен занятым');

    // 2. Порядок ответов. Два запроса в воздухе, отвечаем в обратном порядке —
    //    именно тот, на котором поиск показывает то, что человек уже стёр.
    //    Ответ отменённого запроса всё равно отдаётся: сервер мог успеть ответить
    //    до отсечения, и проверка обязана быть именно на такой случай.
    const race = build({hold: true});
    race.type('Гво');
    await race.flush();
    race.type('Гвоздь');
    await race.flush();
    assert.strictEqual(race.requests.length, 2);
    assert.strictEqual(race.results.getAttribute('aria-busy'), 'true',
        'пока ответ лежит, список должен быть помечен занятым');
    assert.strictEqual(race.requests[0].aborted, true,
        'прежний запрос не отменён: он вернётся вторым и затрёт свежий');
    race.requests[1].deliver();
    await race.settle();
    assert.strictEqual(race.results.innerHTML, race.requests[1].html, 'свежий ответ не применился');
    assert.strictEqual(race.results.getAttribute('aria-busy'), null,
        'список остался помечен занятым');
    race.requests[0].deliver();
    await race.settle();
    assert.strictEqual(race.results.innerHTML, race.requests[1].html,
        'устаревший ответ затер свежий: список показал не то, что в поле');

    // 3. Ручной режим: поиск занял больше порога. Порог — из настройки, здесь
    //    он отгружается по умолчанию, а ниже проверяется, что смена настройки
    //    действительно двигает границу.
    const slow = build({slowMs: 1500});
    slow.type('Гвоздь');
    await slow.flush();
    await slow.wait(1600);
    assert.strictEqual(slow.submit.hidden, false,
        'медленный поиск обязан показать кнопку «Найти»: иначе он тихо ничего не делает');
    const scheduled = slow.timers.length;
    slow.type('Гвозд');
    await slow.flush();
    assert.strictEqual(slow.timers.length, scheduled,
        'в ручном режиме набор не должен слать запросы сам');
    assert.strictEqual(slow.requests.length, 1);

    // 3a. Тот же ответ при поднятом пороге ручного режима не включает его:
    //     иначе значение из настройки не влияло бы ни на что.
    const raised = build({slowMs: 1500, threshold: 5000});
    raised.type('Гвоздь');
    await raised.flush();
    await raised.wait(1600);
    assert.strictEqual(raised.submit.hidden, true,
        'поднятый порог всё равно включил ручной режим: настройка не влияет');

    // 3b. Опущенный порог ниже ответа включает ручной режим — граница именно
    //     там, где её поставили, а не где угадали.
    const lowered = build({slowMs: 500, threshold: 200});
    lowered.type('Гвоздь');
    await lowered.flush();
    await lowered.wait(700);
    assert.strictEqual(lowered.submit.hidden, false,
        'опущенный порог не включил ручной режим при более медленном ответе');

    // 4. Enter не перехватывается ни в одном режиме: форма уходит на страницу
    //    поиска обычной отправкой, и это работает даже без скрипта.
    const auto = build({});
    assert.strictEqual(auto.press('Enter').defaultPrevented, false,
        'скрипт перехватил Enter: отправка формы на страницу поиска невозможна');
    const manual = build({slowMs: 1500});
    manual.type('Гвоздь');
    await manual.flush();
    await manual.wait(1600);
    assert.strictEqual(manual.submit.hidden, false,
        'ручной режим не включился, и проверка Enter ниже ничего не значила бы');
    assert.strictEqual(manual.press('Enter').defaultPrevented, false,
        'в ручном режиме Enter перехвачен, а это единственный путь без кнопки');

    // 5. Открытие по кнопке: окно раскрыто, фокус в поле, каретка в конце.
    const open = build({});
    open.opener.click();
    assert.strictEqual(open.modal.getAttribute('data-open'), 'true');
    assert.strictEqual(open.opener.getAttribute('aria-expanded'), 'true');
    assert.strictEqual(open.field.focused, 1, 'поле не получило фокус при открытии');
    assert.strictEqual(String(open.field.selection), '0,0');
    open.field.value = 'Гвоздь';
    open.opener.click();
    assert.strictEqual(String(open.field.selection), '6,6', 'каретка не встала в конец');
    await open.settle();

    // 6. Escape закрывает и возвращает фокус; второй Escape уже ничего не делает.
    const esc = build({});
    esc.opener.click();
    esc.press('Escape');
    assert.strictEqual(esc.modal.getAttribute('data-open'), 'false');
    assert.strictEqual(esc.opener.getAttribute('aria-expanded'), 'false');
    assert.strictEqual(esc.doc.activeElement, esc.opener, 'фокус не вернулся на лупу');
    esc.press('Escape');
    assert.strictEqual(esc.opener.focused, 1, 'Escape вне окна зря уводит фокус на лупу');

    // 7. Клик мимо панели закрывает, клик внутри — нет; клик по результату закрывает.
    const outside = build({});
    outside.opener.click();
    outside.modal.click(outside.modal);
    assert.strictEqual(outside.modal.getAttribute('data-open'), 'false',
        'клик мимо панели обязан закрывать окно');
    outside.opener.click();
    outside.modal.click(outside.panel);
    assert.strictEqual(outside.modal.getAttribute('data-open'), 'true',
        'клик внутри панели закрыл окно');

    const hit = build({});
    hit.opener.click();
    hit.results.click(link());
    assert.strictEqual(hit.modal.getAttribute('data-open'), 'false',
        'выбранный результат обязан закрыть окно, иначе оно останется на новой странице');
    hit.opener.click();
    hit.results.click(span());
    assert.strictEqual(hit.modal.getAttribute('data-open'), 'true',
        'клик по подписи внутри результата закрыл окно');

    const btn = build({});
    btn.opener.click();
    btn.closeBtn.click();
    assert.strictEqual(btn.modal.getAttribute('data-open'), 'false',
        'кнопка «Закрыть» обязана закрывать окно');

    // 8. Фокус не заперт: скрипт слушает только Escape.
    const free = build({});
    free.opener.click();
    free.press('Tab');
    free.press('ArrowDown');
    assert.strictEqual(free.modal.getAttribute('data-open'), 'true',
        'окно закрылось не на Escape: фокус в нём заперт и с клавиатуры не выйти');
    assert.strictEqual(free.doc.activeElement, free.field, 'фокус ушёл из окна без Escape');

    // 9. Сбой поиска виден, а не выглядит как «ничего не нашлось».
    const broken = build({fail: 'сеть'});
    broken.type('Гвоздь');
    await broken.flush();
    await broken.wait(30);
    assert.ok(/не сработал/.test(broken.results.innerHTML),
        'сбой поиска промолчал: ' + JSON.stringify(broken.results.innerHTML));

    // 10. Закрытие обрывает запрос в воздухе: иначе он вернулся бы в уже закрытое окно.
    const closing = build({hold: true});
    closing.opener.click();
    await closing.flush();
    assert.strictEqual(closing.requests.length, 1);
    closing.press('Escape');
    assert.strictEqual(closing.requests[0].aborted, true,
        'запрос пережил закрытие окна и вернулся бы в него');

    console.log('ok');
})().catch((e) => {
    console.error(e.stack || e.message);
    process.exit(1);
});